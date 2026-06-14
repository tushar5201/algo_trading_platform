import {getMe, login, signup} from "../controllers/auth.controller";
import {authenticate} from "../middleware/auth";
import {asynchandler} from "../middleware/errorhandler";
import {authLimiter} from "../middleware/rateLimiter";
import express from "express";

const router = express.Router();

router.post('/signup', asynchandler(signup));
router.post('/login', asynchandler(login));
router.get('/me', authenticate, asynchandler(getMe));

export default router;
