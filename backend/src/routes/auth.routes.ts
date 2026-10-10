import {getMe, login, signup} from "../controllers/auth.controller";
import {authenticate} from "../middleware/auth";
import {asynchandler} from "../middleware/errorhandler";
import {authLimiter} from "../middleware/rateLimiter";
import express from "express";

const router = express.Router();

router.post('/signup', authLimiter, asynchandler(signup));
router.post('/login', authLimiter, asynchandler(login));
router.get('/me', authenticate, asynchandler(getMe));

export default router;
