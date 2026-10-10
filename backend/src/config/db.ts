import mongoose from 'mongoose';
import { env } from './env';

export const connectDB = async (): Promise<void> => {
    try {
        await mongoose.connect(env.MONGO_URI);
        console.log('MongoDB connected');
    } catch (err) {
        console.error('MongoDB connection failed:', (err as Error).message);
        process.exit(1);
    }
};