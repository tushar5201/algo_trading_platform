import { KillSwitch } from "../models/KillSwitch";
import { User } from "../models/User";
import { publishStrategyRunForAllUsers } from "../services/queue.service";

const INTERVAL_MS = 1 * 60 * 1000; // 5 minutes

export const startScheduler = async (): Promise<void> => {
    console.log(`[Scheduler] Starting Node Scheduler with interval of ${INTERVAL_MS / 60000} minutes`);
    const run = async (): Promise<void> => {
        try {
            const globalKill = await KillSwitch.findOne({ scope: 'global', isActive: true });
            if (globalKill) {
                console.log(`[Scheduler] Global kill switch is active. Skipping this run.`);
                return;
            }

            // Find all elegible users 
            const users = await User.find({
                status: 'APPROVED',
                autoTrading: true,
                isActive: true,
                encryptedCredentials: { $ne: null },
                assignedStrategies: { $not: { $size: 0 } },
            }).select('_id email assignedStrategies encryptedCredentials autoTrading');

            if (users.length === 0) {
                console.log(`[Scheduler] No eligible users found for strategy run.`);
                return;
            }

            // Flatten: one job per user per strategy
            const jobs = users.flatMap(user =>
                user.assignedStrategies.map(strategyName => ({
                    userId: user._id.toString(),
                    userEmail: user.email,
                    strategyName,
                    encryptedCredentials: user.encryptedCredentials!,
                    autoTrading: user.autoTrading,
                }))
            );

            await publishStrategyRunForAllUsers(jobs, 'NODE_SCHEDULER');
            console.log(`[Scheduler] Queued ${jobs.length} strategy jobs for ${users.length} users`);
        } catch (error) {
            console.error('[Scheduler] Error in scheduler run:', error);
        }
    }
    await run();
    setInterval(run, INTERVAL_MS);
}