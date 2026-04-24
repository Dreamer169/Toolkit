/**
 * PersistenceManager — 基于 MemoryManager 适配
 * 将任务状态持久化到 PostgreSQL，解决重启后任务丢失问题
 */
import { execute, query } from '../db.js';

export interface JobSnapshot {
  jobId: string;
  status: string;
  startedAt: number;
  logs: Array<{ type: string; message: string }>;
  accounts: Array<{ email: string; password: string; username?: string; token?: string }>;
  exitCode: number | null;
}

export class PersistenceManager {
  private static initialized = false;

  static async init(): Promise<void> {
    if (this.initialized) return;
    await execute(`
      CREATE TABLE IF NOT EXISTS job_snapshots (
        job_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'running',
        started_at BIGINT NOT NULL,
        logs JSONB NOT NULL DEFAULT '[]',
        accounts JSONB NOT NULL DEFAULT '[]',
        exit_code INT,
        updated_at TIMESTAMPTZ DEFAULT NOW()
      )
    `);
    this.initialized = true;
  }

  static async save(job: JobSnapshot): Promise<void> {
    await this.init();
    await execute(
      `INSERT INTO job_snapshots (job_id, status, started_at, logs, accounts, exit_code, updated_at)
       VALUES ($1, $2, $3, $4, $5, $6, NOW())
       ON CONFLICT (job_id) DO UPDATE SET
         status = EXCLUDED.status,
         logs = EXCLUDED.logs,
         accounts = EXCLUDED.accounts,
         exit_code = EXCLUDED.exit_code,
         updated_at = NOW()`,
      [job.jobId, job.status, job.startedAt, JSON.stringify(job.logs), JSON.stringify(job.accounts), job.exitCode]
    );
  }

  static async load(jobId: string): Promise<JobSnapshot | null> {
    await this.init();
    const rows = await query<Record<string, unknown>>(
      'SELECT * FROM job_snapshots WHERE job_id = $1',
      [jobId]
    );
    if (!rows.length) return null;
    const r = rows[0];
    return {
      jobId: r.job_id as string,
      status: r.status as string,
      startedAt: Number(r.started_at),
      logs: r.logs as JobSnapshot['logs'],
      accounts: r.accounts as JobSnapshot['accounts'],
      exitCode: r.exit_code as number | null,
    };
  }

  static async loadAll(): Promise<JobSnapshot[]> {
    await this.init();
    const rows = await query<Record<string, unknown>>(
      'SELECT * FROM job_snapshots ORDER BY started_at DESC LIMIT 100'
    );
    return rows.map(r => ({
      jobId: r.job_id as string,
      status: r.status as string,
      startedAt: Number(r.started_at),
      logs: r.logs as JobSnapshot['logs'],
      accounts: r.accounts as JobSnapshot['accounts'],
      exitCode: r.exit_code as number | null,
    }));
  }

  static async delete(jobId: string): Promise<void> {
    await this.init();
    await execute('DELETE FROM job_snapshots WHERE job_id = $1', [jobId]);
  }

  /**
   * reapOrphans — 启动时调用：所有遗留 status='running' 的行其实没有进程在跑
   * （子进程是 api-server 的 child；node 一重启就 SIGTERM 掉了；DB 行没人收尾）。
   * 把它们标记成 'crashed' + exitCode=-99 + 追加一条说明日志，前端不再撒谎。
   */
  static async reapOrphans(): Promise<number> {
    await this.init();
    const rows = await query<{ job_id: string }>(
      `UPDATE job_snapshots
         SET status = 'crashed',
             exit_code = -99,
             logs = logs || '[{"type":"error","message":"⚠ api-server 重启时此任务仍在 running，子进程已丢失，自动标记为 crashed"}]'::jsonb,
             updated_at = NOW()
       WHERE status = 'running'
       RETURNING job_id`
    );
    return rows.length;
  }
}
