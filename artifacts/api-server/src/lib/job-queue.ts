/**
 * JobQueue — 基于 MessageBroker/EventBus 适配
 * 内存 + PostgreSQL 双层任务队列，重启后自动恢复
 */
import { PersistenceManager, type JobSnapshot } from "./persistence-manager.js";

type LogEntry = { type: string; message: string };
type Account  = { email: string; password: string; username?: string; token?: string };

export interface Job extends JobSnapshot {
  _child?: { kill: () => void };
}

type Subscriber = (job: Job, event: string) => void;

class JobQueue {
  private jobs = new Map<string, Job>();
  private subscribers = new Map<string, Subscriber[]>();
  private lastActivity = new Map<string, number>();
  private watchdogTimer: NodeJS.Timeout | null = null;

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
      status: "running",
      startedAt: Date.now(),
      logs: [],
      accounts: [],
      exitCode: null,
    };
    this.jobs.set(jobId, job);
    this.lastActivity.set(jobId, Date.now());
    await PersistenceManager.save(job);
    return job;
  }

  /** 追加日志并异步持久化 */
  pushLog(jobId: string, entry: LogEntry): void {
    const job = this.jobs.get(jobId);
    if (!job) return;
    this.lastActivity.set(jobId, Date.now());
    job.logs.push(entry);
    this.emit("log", job);
    // 每 10 条或遇到 done/error 时写库
    if (job.logs.length % 10 === 0 || entry.type === "done" || entry.type === "error") {
      PersistenceManager.save(job).catch(() => {});
    }
  }

  pushAccount(jobId: string, acc: Account): void {
    const job = this.jobs.get(jobId);
    if (!job) return;
    this.lastActivity.set(jobId, Date.now());
    job.accounts.push(acc);
  }

  async finish(jobId: string, exitCode: number, status = "done"): Promise<void> {
    const job = this.jobs.get(jobId);
    if (!job) return;
    job.status     = status;
    job.exitCode   = exitCode;
    job.finishedAt = Date.now();
    this.lastActivity.delete(jobId);
    await PersistenceManager.save(job);
    this.emit("status_change", job);
    this.emit("done", job);
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
    job.status = "stopped";
    job.logs.push({ type: "warn", message: "⚠ 用户停止了任务" });
    PersistenceManager.save(job).catch(() => {});
    return true;
  }

  /** 彻底删除任务（内存 + DB） */
  async remove(jobId: string): Promise<boolean> {
    const job = this.jobs.get(jobId);
    if (job) {
      try { (job as Job & { _child?: { kill(): void } })._child?.kill(); } catch {}
      this.jobs.delete(jobId);
    }
    await PersistenceManager.delete(jobId).catch(() => {});
    return true;
  }

  /** 批量删除0成功的已完成任务 */
  async bulkPurge(opts: { onlyZeroAccounts?: boolean } = {}): Promise<number> {
    const all = await this.list();
    let count = 0;
    for (const job of all) {
      const isDone = ["done","stopped","failed","crashed"].includes(job.status);
      const isZero = (job.accounts?.length ?? 0) === 0;
      if (isDone && (!opts.onlyZeroAccounts || isZero)) {
        await this.remove(job.jobId);
        count++;
      }
    }
    return count;
  }

  /**
   * startWatchdog — 定期扫描内存中处于 running 状态但长时间无任何活动
   * （无新日志/无新账号）的任务，强制标记为 failed 并 kill 掉遗留子进程。
   * 解决 Bug: child.on("close") 因孙进程持有 stdio 管道而永不触发，
   * 导致任务状态永久卡在 running（exitCode: null）。
   */
  startWatchdog(timeoutMs = 5 * 60 * 1000, intervalMs = 60 * 1000): void {
    if (this.watchdogTimer) return;
    this.watchdogTimer = setInterval(() => {
      const now = Date.now();
      for (const [jobId, job] of this.jobs) {
        if (job.status !== "running") continue;
        const last = this.lastActivity.get(jobId) ?? job.startedAt;
        if (now - last > timeoutMs) {
          try { (job as Job & { _child?: { kill(): void } })._child?.kill(); } catch {}
          job.logs.push({
            type: "error",
            message: `⚠ [watchdog] 任务超过 ${Math.round(timeoutMs / 1000)}s 无任何活动，判定为卡死子进程未正常退出（stdio 管道未闭合），自动标记为 failed`,
          });
          job.status = "failed";
          job.exitCode = -98;
          job.finishedAt = now;
          this.lastActivity.delete(jobId);
          PersistenceManager.save(job).catch(() => {});
          this.emit("status_change", job);
          this.emit("done", job);
        }
      }
    }, intervalMs);
    this.watchdogTimer.unref?.();
  }

  stopWatchdog(): void {
    if (this.watchdogTimer) { clearInterval(this.watchdogTimer); this.watchdogTimer = null; }
  }

  setChild(jobId: string, child: { kill: () => void }): void {
    const job = this.jobs.get(jobId);
    if (job) (job as Job & { _child: typeof child })._child = child;
  }
}

export const jobQueue = new JobQueue();
