// Put the InfluxDB token (from .env / docker-compose) into Node-RED's encrypted
// credentials file before Node-RED starts, so no secret has to live in flows.json
// or in Git.  Uses the same scheme as Node-RED itself
// (@node-red/runtime/lib/nodes/credentials.js: aes-256-ctr, key = sha256(secret)).
// Credentials of other nodes already in the file are kept.
const crypto = require('crypto')
const fs = require('fs')

const FILE = '/data/flows_cred.json'
const secret = process.env.NODE_RED_CREDENTIAL_SECRET || 'solar-sim-credential-secret'
const key = crypto.createHash('sha256').update(secret).digest()

function decrypt (obj) {
    const s = obj.$
    const iv = Buffer.from(s.substring(0, 32), 'hex')
    const d = crypto.createDecipheriv('aes-256-ctr', key, iv)
    return JSON.parse(d.update(s.substring(32), 'base64', 'utf8') + d.final('utf8'))
}

function encrypt (creds) {
    const iv = crypto.randomBytes(16)
    const c = crypto.createCipheriv('aes-256-ctr', key, iv)
    return { $: iv.toString('hex') + c.update(JSON.stringify(creds), 'utf8', 'base64') + c.final('base64') }
}

let creds = {}
if (fs.existsSync(FILE)) {
    try {
        const raw = JSON.parse(fs.readFileSync(FILE, 'utf8'))
        creds = raw.$ ? decrypt(raw) : raw
    } catch (e) {
        console.warn('seed-credentials: could not read existing credentials (secret changed?) - recreating')
        creds = {}
    }
}

// config node ids used in flows.json
creds.cfg_influxdb = { token: process.env.INFLUX_TOKEN || '' }

fs.writeFileSync(FILE, JSON.stringify(encrypt(creds), null, 4))
console.log('seed-credentials: InfluxDB token written to flows_cred.json')
