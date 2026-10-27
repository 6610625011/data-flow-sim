/**
 * Node-RED settings for the (simulated) Raspberry Pi.
 * Everything secret comes from environment variables set in docker-compose.yml / .env:
 *
 *   NODE_RED_CREDENTIAL_SECRET  key that encrypts flows_cred.json
 *   NODE_RED_ADMIN_USER/PASSWORD  login for the flow editor  (http://<pi>:1880/)
 *   DASHBOARD_USER/PASSWORD     optional login for the dashboard (/dashboard) and /api
 *
 * Unset passwords switch the corresponding login off.
 */
const bcrypt = require('bcryptjs') // ships with Node-RED (NODE_PATH in the docker image)

const env = process.env
const hash = (pw) => bcrypt.hashSync(pw, 8)

module.exports = {
    httpStatic: "/data/public/",
    flowFile: 'flows.json',
    flowFilePretty: true,
    credentialSecret: env.NODE_RED_CREDENTIAL_SECRET || 'solar-sim-credential-secret',

    uiPort: process.env.PORT || 1880,

    adminAuth: env.NODE_RED_ADMIN_PASSWORD
        ? {
              type: 'credentials',
              users: [{ username: env.NODE_RED_ADMIN_USER || 'admin', password: hash(env.NODE_RED_ADMIN_PASSWORD), permissions: '*' }]
          }
        : undefined,

    httpNodeAuth: env.DASHBOARD_PASSWORD
        ? { user: env.DASHBOARD_USER || 'group', pass: hash(env.DASHBOARD_PASSWORD) }
        : undefined,

    // the experiment label and counters survive a Node-RED restart (store "file")
    contextStorage: {
        default: { module: 'memory' },
        file: { module: 'localfilesystem', config: { flushInterval: 10 } }
    },

    functionExternalModules: false,
    functionTimeout: 0,
    exportGlobalContextKeys: false,
    debugMaxLength: 1000,

    logging: { console: { level: 'info', metrics: false, audit: false } },

    editorTheme: {
        header: { title: 'Solar Pi (simulated) - Node-RED' },
        projects: { enabled: false },
        palette: {},
        tours: false
    },

    diagnostics: { enabled: true, ui: true },
    runtimeState: { enabled: false, ui: false },
    telemetry: { enabled: false }
}
