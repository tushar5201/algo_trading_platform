process.env.NODE_ENV = 'test';
process.env.PORT = '3000';
process.env.JWT_SECRET = 'test-jwt-secret-please-change-1234567890';
process.env.JWT_EXPIRES_IN = '7d';
process.env.MONGO_URI = 'mongodb://localhost:27017/test-db';
process.env.REDIS_HOST = 'localhost';
process.env.REDIS_PORT = '6379';
process.env.AES_SECRET_KEY = '12345678901234567890123456789012';