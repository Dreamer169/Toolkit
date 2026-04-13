/**
 * PersistenceManager — 基于 MemoryManager 适配
 * 将任务状态持久化到 PostgreSQL，解决重启后任务丢失问题
 */
import { execute } from '../db.js';

export interface JobSnapshot {
  jobId: string;
  status: string;
  startedAt: number;
  logs: Array<{ type: string; message: string }>;
  accounts: Array<{ email: string; password: string }>;
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
    const rows = await execute(
      'SELECT * FROM job_snapshots WHERE job_id = $1',
      [jobId]
    ) as Array<Record<string, unknown>>;
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
    const rows = await execute(
      'SELECT * FROM job_snapshots ORDER BY started_at DESC LIMIT 100'
    ) as Array<Record<string, unknown>>;
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
}
