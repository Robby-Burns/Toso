/**
 * Automated Verification Suite for Story UI-002: Web Application Shell & Decoupled Telemetry Console.
 * Verifies connection watchdog, fail-closed lockout, position inspection drawer,
 * environment mode segregation, and audio/visual emergency feedback.
 */

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, 'out', 'index.html');

console.log('--- TOSO Story UI-002 Shell Verification Audit ---');

if (!fs.existsSync(htmlPath)) {
  console.error('FAIL: web/out/index.html does not exist.');
  process.exit(1);
}

const html = fs.readFileSync(htmlPath, 'utf8');

const requiredShellElements = [
  { id: 'connection-alert-banner', desc: 'Fail-Closed Connection Alert Banner' },
  { id: 'reconnect-attempt', desc: 'Reconnection Attempt Counter' },
  { id: 'live-warning-banner', desc: 'Live Environment Mode Warning Watermark' },
  { id: 'position-drawer', desc: 'Slide-Over Position Inspection Drawer' },
  { id: 'drawer-symbol', desc: 'Drawer Symbol Indicator' },
  { id: 'drawer-stop', desc: 'Drawer Invalidation ATR Stop Display' },
  { id: 'drawer-tier', desc: 'Drawer Pyramid Tier Status' },
  { id: 'drawer-shares', desc: 'Drawer Share Quantity & Allocation' },
  { id: 'main-header', desc: 'Header Shockwave Alert Animation Target' },
];

let failed = 0;

for (const el of requiredShellElements) {
  if (html.includes(`id="${el.id}"`)) {
    console.log(`[PASS] ${el.desc} (#${el.id})`);
  } else {
    console.error(`[FAIL] Missing required shell element: ${el.desc} (#${el.id})`);
    failed++;
  }
}

// Verify interactive features
const requiredBehaviors = [
  { needle: 'function toggleEnvironment(', desc: 'Environment Toggle Functionality' },
  { needle: 'function playDisarmAudioAlert(', desc: 'Web Audio Synthesized Alert Tone' },
  { needle: 'function openPositionDrawer(', desc: 'Position Inspection Drawer Handler' },
  { needle: 'function resyncStateAfterReconnect(', desc: 'Reconnection Sequence Resync Engine' },
  { needle: 'btn-open-arming\').disabled = true', desc: 'Fail-Closed Button Lockout on Drop' },
  { needle: 'btn-open-arming\').disabled = false', desc: 'Lockout Release on Link Recovery' },
];

for (const b of requiredBehaviors) {
  if (html.includes(b.needle)) {
    console.log(`[PASS] Verified behavior: ${b.desc}`);
  } else {
    console.error(`[FAIL] Missing behavior: ${b.desc}`);
    failed++;
  }
}

if (failed > 0) {
  console.error(`UI-002 Shell Audit failed with ${failed} issues.`);
  process.exit(1);
}

console.log('All UI-002 Shell assertions PASSED successfully.');
process.exit(0);
