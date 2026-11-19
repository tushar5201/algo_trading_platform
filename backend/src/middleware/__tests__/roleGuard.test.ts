import { describe, expect, it } from "@jest/globals";
import { requireRole } from "../roleGuard";

const req = (user?: unknown) => ({ user }) as any;

describe("requireRole", () => {
    it("401 when no user is attached to the request", async () => {
        await expect(requireRole("admin")(req())).rejects.toMatchObject({ statusCode: 401 });
    });

    it("403 when the role is not allowed", async () => {
        await expect(requireRole("admin")(req({ userId: "u1", role: "user", email: "a@b.c" })))
            .rejects.toMatchObject({ statusCode: 403, message: expect.stringMatching(/permission/i) });
    });

    it("resolves when the role matches", async () => {
        await expect(requireRole("admin")(req({ role: "admin" }))).resolves.toBeUndefined();
    });

    it("accepts any of several allowed roles", async () => {
        const guard = requireRole("admin", "user");
        await expect(guard(req({ role: "user" }))).resolves.toBeUndefined();
        await expect(guard(req({ role: "admin" }))).resolves.toBeUndefined();
    });

    it("rejects an unknown role", async () => {
        await expect(requireRole("admin", "user")(req({ role: "guest" }))).rejects.toMatchObject({ statusCode: 403 });
    });
});
