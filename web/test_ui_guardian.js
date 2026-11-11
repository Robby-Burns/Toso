/**
 * Automated Verification Suite for Story UI-003: 5-State Guardian & Risk Inspector.
 * Verifies Black Swan Capital Isolation Vault, Defensive Universe Mutual Exclusivity,
 * Concentration Gauges, Valid Winner Immunity, and Fail-Closed Simulation Toolbar.
 */

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, 'out', 'index.html');

console.log('--- TOSO Story UI-003 Guardian Inspector Verification Audit ---');

if (!fs.existsSync(htmlPath)) {
  console.error('FAIL: web/out/index.html does not exist.');
  process.exit(1);
}

const html = fs.readFileSync(htmlPath, 'utf8');

const requiredGuardianElements = [
  { id: 'card-black-swan-vault', desc: 'Black Swan Defense & Capital Isolation Vault' },
  { id: 'badge-mutual-exclusivity', desc: 'Universe Mutual Exclusivity Audit Badge' },
  { id: 'defense-capital-val', desc: 'Ring-Fenced 5% Defense Capital Card' },
  { id: 'defense-trigger-status', desc: 'Tail-Risk Autonomous Trigger State' },
  { id: 'defense-universe-tbody', desc: 'Approved Defensive ETP Universe Catalog Table' },
  { id: 'card-concentration-bars', desc: 'Single-Stock Concentration & Winner Immunity Card' },
  { id: 'guardian-simulator-panel', desc: 'Interactive Fail-Closed Simulator Panel' },
  { id: 'guardian-checks-list', desc: '5-State Guardian Rulebook & Evidence List' },
];

let failed = 0;

for (const el of requiredGuardianElements) {
  if (html.includes(`id="${el.id}"`)) {
    console.log(`[PASS] ${el.desc} (#${el.id})`);
  } else {
    console.error(`[FAIL] Missing required Guardian element: ${el.desc} (#${el.id})`);
    failed++;
  }
}

// Verify Black Swan & Winner Invariant keywords
const requiredInvariants = [
  { needle: 'INVARIANT INV-012/013', desc: 'Black Swan Isolation Invariant Citation' },
  { needle: 'VALID WINNER: PROTECTED UNDER CONTINUATION', desc: 'Winner Concentration Protection (INV-004)' },
  { needle: 'STALE_FEED', desc: 'Stale Feed Simulation Action' },
  { needle: 'CLOCK_SKEW', desc: 'Clock Skew Simulation Action' },
  { needle: 'CONCENTRATION_HOLD', desc: 'Intake Hold Simulation Action' },
  { needle: '/api/guardian/defense', desc: 'Defense Endpoint Wiring' },
  { needle: '/api/guardian/simulate', desc: 'Simulation Endpoint Wiring' },
];

for (const inv of requiredInvariants) {
  if (html.includes(inv.needle)) {
    console.log(`[PASS] Verified Invariant / API: ${inv.desc}`);
  } else {
    console.error(`[FAIL] Missing Invariant / API: ${inv.desc}`);
    failed++;
  }
}

if (failed > 0) {
  console.error(`UI-003 Guardian Audit failed with ${failed} issues.`);
  process.exit(1);
}

console.log('All UI-003 Guardian & Black Swan assertions PASSED successfully.');
process.exit(0);
