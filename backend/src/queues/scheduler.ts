import {KillSwitch} from "../models/KillSwitch";
import {User} from "../models/User";
import {publishStrategyRunForAllUsers} from "../services/queue.service";
import {clearInterval} from "node:timers";
import {Strategy} from "../models/Strategy";

const INTERVAL_MS = 60 * 1000; // 1 minutes

let timer: NodeJS.Timeout | null = null;
let running = false;
let stopped = false;

const run = async (): Promise<void> => {
    if (running) return;
    running = true;
    try {
        const globalKill = await KillSwitch.findOne({scope: 'global', isActive: true});
        if (globalKill) {
            console.log(`[Scheduler] Global kill switch is active. Skipping this run.`);
            return;
        }

        // Find all elegible users
        const users = await User.find({
            status: 'APPROVED',
            autoTrading: true,
            isActive: true,
            encryptedCredentials: {$ne: null},
            assignedStrategies: {$not: {$size: 0}},
        }).select('_id email assignedStrategies +encryptedCredentials autoTrading');

        if (users.length === 0) {
            console.log(`[Scheduler] No eligible users found for strategy run.`);
            return;
        }

        const [userKills, activeStrategies] = await Promise.all([
            KillSwitch.find({scope: 'user', isActive: true}).select('userId'),
            Strategy.find({isActive: true}).select('name'),
        ]);

        const killedUsers = new Set(userKills.map(k => k.userId?.toString()));
        const activeNames = new Set(activeStrategies.map(strategy => strategy.name));

        // Flatten: one job per user per strategy
        const jobs = users
            .filter(user => !killedUsers.has(user._id.toString()))
            .flatMap(user =>
                user.assignedStrategies
                    .filter(strategyName => activeNames.has(strategyName))
                    .map(strategyName => ({
                        userId: user._id.toString(),
                        userEmail: user.email,
                        strategyName,
                        encryptedCredentials: user.encryptedCredentials!,
                        autoTrading: user.autoTrading,
                    }))
            );

        if (jobs.length === 0) {
            console.log(`[Scheduler] No runnable jobs after kill-switch / active-strategy filtering.`);
            return;
        }

        await publishStrategyRunForAllUsers(jobs, 'NODE_SCHEDULER');
        console.log(`[Scheduler] Queued ${jobs.length} strategy jobs for ${users.length} users`);
    } catch (error) {
        console.error('[Scheduler] Error in scheduler run:', error);
    } finally {
        running = false;
    }
}

export const startScheduler = async (): Promise<void> => {
    stopped = false;
    console.log(`[Scheduler] Starting Node Scheduler with interval of ${INTERVAL_MS / 60000} minutes`);
    await run();
    if (stopped) return;
    timer = setInterval(() => void run(), INTERVAL_MS);
};

export const stopScheduler = (): void => {
    stopped = true;
    if (timer) {
        clearInterval(timer);
        timer = null;
    }
};
