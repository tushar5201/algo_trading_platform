import bcrypt from "bcryptjs";
import { IUser } from "../models/User";
import jwt from "jsonwebtoken";
import {env} from '../config/env';

const SALT_ROUNDS = 12;

export const hashPassword = (password: string): Promise<string> =>
    bcrypt.hash(password, SALT_ROUNDS);

export const comparePassword = (password: string, hash: string): Promise<boolean> =>
    bcrypt.compare(password, hash);

export interface JwtPayload {
    userId: string;
    role: string;
    email: string;
}

export const signToken = (user: IUser): string =>
    jwt.sign(
        { userId: user._id.toString(), role: user.role, email: user.email },
        env.JWT_SECRET,
        {expiresIn: env.JWT_EXPIRES_IN} as jwt.SignOptions
    );

export const verifyToken = (token: string): JwtPayload => 
    jwt.verify(token, env.JWT_SECRET) as JwtPayload;
