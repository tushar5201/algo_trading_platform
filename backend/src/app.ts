import dotenv from "dotenv";
import express from "express";
import helmet from "helmet";
import cors from "cors";
import morgan from "morgan";
import {apiLimiter} from "./middleware/rateLimiter";
import authRoutes from "./routes/auth.routes";
import { errorHandler } from "./middleware/errorhandler";
import adminRoutes from "./routes/admin.routes";
import userRoutes from "./routes/user.routes";
import tradesRoutes from "./routes/trades.routes";
import {env} from "./config/env";

dotenv.config();
const app = express();

app.use(helmet());
app.use(cors({ origin: env.FRONTEND_URL ?? (env.NODE_ENV === 'production' ? false : true) }));

app.use(express.json({ limit: '10kb' }));
app.use(express.urlencoded({ extended: true, limit: '10kb' }));

app.use(morgan(env.NODE_ENV === 'production' ? 'combined': 'dev'));

app.set("trust proxy", 1);

app.use("/api", apiLimiter);

app.get("/api/health", (req, res) => {
    res.json({ success: true, status: "ok", time: new Date().toISOString() });
});

app.use("/api/auth", authRoutes);
app.use("/api/admin", adminRoutes);
app.use("/api/user", userRoutes);
app.use("/api/trades", tradesRoutes);

app.use((req,res) => {
    res.status(404).json({ message: "Route not found" });
});

app.use(errorHandler);

export default app;
