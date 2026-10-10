import {strategyQueue} from "../queues";
import {StrategyRunJob} from "../queues/jobTypes";

const minuteBucket = (): number => Math.floor(Date.now() / 60000);

// Publish: Run a strategy for a user
export const publishStrategyRun = async (payload: Omit<StrategyRunJob, 'jobType' | 'timestamp'>): Promise<string> => {
    const job = await strategyQueue.add(`strategy: ${payload.strategyName}:${payload.userId}`, {
            ...payload,
            jobType: 'STRATEGY_RUN',
            timestamp: new Date().toISOString(),
        } satisfies StrategyRunJob,
        {
            jobId: `manual-${payload.userId}-${payload.strategyName}-${Date.now()}`,
        });
    console.log(`[Queue] Published strategy run job with ID: ${job.id} for user: ${payload.userId}`);
    return job.id!;
}

// Publish: All active users for a strategy
// Called by the scheduler every n minutes
export const publishStrategyRunForAllUsers = async (
    users: Array<{
        userId: string;
        userEmail: string;
        strategyName: string;
        encryptedCredentials: string;
        autoTrading: boolean;
    }>,
    triggeredBy: StrategyRunJob['triggeredBy'] = 'NODE_SCHEDULER'
): Promise<void> => {
    const bucket = minuteBucket();
    const jobs = users.map(user => ({
        name: `strategy: ${user.strategyName}:${user.userId}`,
        data: {
            ...user,
            jobType: 'STRATEGY_RUN' as const,
            timestamp: new Date().toISOString(),
            triggeredBy,
        } satisfies StrategyRunJob,
        opts: {
            jobId: `${user.userId}-${user.strategyName}-${bucket}`,
        }
    }));
    await strategyQueue.addBulk(jobs);
    console.log(`[Queue] Bulk published ${jobs.length} strategy jobs`);
}
