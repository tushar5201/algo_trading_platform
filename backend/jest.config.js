
module.exports = {
    testEnvironment: 'node',
    rootDir: '.',
    transform: {
        '^.+\\.tsx?$': ['ts-jest', { tsconfig: 'tsconfig.test.json' }],
    },
    testMatch: ['<rootDir>/src/**/__tests__/**/*.test.ts'],
    setupFiles: ['<rootDir>/src/__tests__/setupEnv.ts'],
    clearMocks: true,
    collectCoverageFrom: [
        'src/**/*.ts',
        '!src/**/__tests__/**',
        '!src/server.ts',
    ],
    coverageDirectory: '<rootDir>/coverage',
    verbose: true
};
