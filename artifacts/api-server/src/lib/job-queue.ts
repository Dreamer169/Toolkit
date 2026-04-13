/**
 * JobQueue — 基于 MessageBroker/EventBus 适配
 * 内存 + PostgreSQL 双层任务队列，重启后自动恢复
 */
import { PersistenceManager, type JobSnapshot } from './persistence-manager.js';

type LogEntry = { type: string; message: string };
type Account  = { email: string; password: string };

export interface Job extends JobSnapshot {
  _child?: { kill: () => void };
}

type Subscriber = (job: Job, event: string) => void;

class JobQueue {
  private jobs = new Map<string, Job>();
  private subscribers = new Map<string, Subscriber[]>();

  /** 订阅事件（log / status_change / done） */
  subscribe(event: string, cb: Subscriber): void {
    if (!this.subscribers.has(event)) this.subscribers.set(event, []);
    this.subscribers.get(event)!.push(cb);
  }

  private emit(event: string, job: Job): void {
    for (const cb of this.subscribers.get(event) ?? []) {
      try { cb(job, event); } catch {}
    }
  }

  /** 新建任务（内存 + DB） */
  async create(jobId: string): Promise<Job> {
    const job: Job = {
      jobId,
      status: 'running',
      startedAt: Date.now(),
      logs: [],
      accounts: [],
      exitCode: null,
    };
    this.jobs.set(jobId, job);
    await PersistenceManager.save(job);
    return job;
  }

  /** 追加日志并异步持久化 */
  pushLog(jobId: string, entry: LogEntry): void {
    const job = this.jobs.get(jobId);
    if (!job) return;
    job.logs.push(entry);
    this.emit('log', job);
    // 每 10 条或遇到 done/error 时写库
    if (job.logs.length % 10 === 0 || entry.type === 'done' || entry.type === 'error') {
      PersistenceManager.save(job).catch(() => {});
    }
  }

  pushAccount(jobId: string, acc: Account): void {
    const job = this.jobs.get(jobId);
    if (!job) return;
    job.accounts.push(acc);
  }

  async finish(jobId: string, exitCode: number, status = 'done'): Promise<void> {
    const job = this.jobs.get(jobId);
    if (!job) return;
    job.status  = status;
    job.exitCode = exitCode;
    await PersistenceManager.save(job);
    this.emit('status_change', job);
    this.emit('done', job);
  }

  /** 获取任务（先查内存，再查 DB） */
  async get(jobId: string): Promise<Job | null> {
    if (this.jobs.has(jobId)) return this.jobs.get(jobId)!;
    const snap = await PersistenceManager.load(jobId);
    if (snap) { this.jobs.set(snap.jobId, snap as Job); return snap as Job; }
    return null;
  }

  /** 列出所有任务（内存 + DB 合并） */
  async list(): Promise<Job[]> {
    const dbJobs = await PersistenceManager.loadAll();
    for (const j of dbJobs) {
      if (!this.jobs.has(j.jobId)) this.jobs.set(j.jobId, j as Job);
    }
    const all = Array.from(this.jobs.values());
    all.sort((a, b) => b.startedAt - a.startedAt);
    return all;
  }

  stop(jobId: string): boolean {
    const job = this.jobs.get(jobId);
    if (!job) return false;
    try { (job as Job & { _child?: { kill(): void } })._child?.kill(); } catch {}
    job.status = 'stopped';
    job.logs.push({ type: 'warn', message: '⚠ 用户停止了任务' });
    PersistenceManager.save(job).catch(() => {});
    return true;
  }

  setChild(jobId: string, child: { kill: () => void }): void {
    const job = this.jobs.get(jobId);
    if (job) (job as Job & { _child: typeof child })._child = child;
  }
}

export const jobQueue = new JobQueue();
