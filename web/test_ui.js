/**
 * Automated Verification Script for TOSO Web Console Assets.
 * Verifies DOM structure, decoupled top-bar telemetry, 80/15/5 capital elements,
 * 5-state Guardian representation, and the Two-Step Arming Slider.
 */

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, 'out', 'index.html');

console.log('--- TOSO Web UI Verification Audit ---');

if (!fs.existsSync(htmlPath)) {
  console.error('FAIL: web/out/index.html does not exist.');
  process.exit(1);
}

const html = fs.readFileSync(htmlPath, 'utf8');

const requiredElements = [
  { id: 'pill-env', desc: 'Decoupled Environment Pill' },
  { id: 'pill-auth', desc: 'Decoupled Execution Authorization Pill' },
  { id: 'pill-broker', desc: 'Decoupled Broker Link Heartbeat Pill' },
  { id: 'pill-reconcile', desc: 'Decoupled Reconciliation Pill' },
  { id: 'btn-emergency-disarm', desc: '1-Click Zero-Friction Emergency Disarm Button' },
  { id: 'cap-core', desc: '80% Core Capital Allocation Card' },
  { id: 'cap-reserve', desc: '15% Reserve Cash Allocation Card' },
  { id: 'cap-bs', desc: '5% Black Swan Defense Allocation Card' },
  { id: 'gate-domain-feed', desc: 'Guardian System & Feed Domain' },
  { id: 'gate-domain-risk', desc: 'Guardian Intake & Risk Domain' },
  { id: 'gate-domain-exec', desc: 'Guardian Execution Safety Domain' },
  { id: 'slider-track', desc: 'Two-Step Slide-to-Arm Gesture Track' },
  { id: 'slider-thumb', desc: 'Two-Step Slide-to-Arm Gesture Thumb' },
  { id: 'telemetry-log', desc: 'Real-time WebSocket Telemetry Stream Log' },
];

let failed = 0;

for (const el of requiredElements) {
  if (html.includes(`id="${el.id}"`)) {
    console.log(`[PASS] ${el.desc} (#${el.id})`);
  } else {
    console.error(`[FAIL] Missing required element: ${el.desc} (#${el.id})`);
    failed++;
  }
}

// Verify WebSocket integration
if (html.includes("new WebSocket(") && html.includes("/ws")) {
  console.log('[PASS] Sequenced WebSocket integration verified.');
} else {
  console.error('[FAIL] WebSocket connection logic missing.');
  failed++;
}

// Verify Emergency Disarm calls /api/emergency/disarm
if (html.includes("/api/emergency/disarm")) {
  console.log('[PASS] Instant Emergency Disarm API wiring verified.');
} else {
  console.error('[FAIL] Emergency Disarm API wiring missing.');
  failed++;
}

if (failed > 0) {
  console.error(`Audit failed with ${failed} missing components.`);
  process.exit(1);
}

console.log('All Web Console UI assertions PASSED successfully.');
process.exit(0);
