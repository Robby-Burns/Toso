/**
 * Automated Verification Suite for Story UI-004: Scientist Studio & Proposal Diff Workbench.
 * Verifies Active Production Config Inspection, Candidate Research Proposals,
 * Backtest Performance Metric Diffs, Comparative Equity Curves, Side-by-Side YAML Diff,
 * and Cryptographic Human Approval & Atomic Adoption Gates (INV-014, INV-015).
 */

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, 'out', 'index.html');

console.log('--- TOSO Story UI-004 Scientist Studio & Proposal Diff Workbench Audit ---');

if (!fs.existsSync(htmlPath)) {
  console.error('FAIL: web/out/index.html does not exist.');
  process.exit(1);
}

const html = fs.readFileSync(htmlPath, 'utf8');

const requiredElements = [
  { id: 'tab-scientist', desc: 'Scientist Studio Main Workspace Container' },
  { id: 'badge-research-isolation', desc: 'INV-014 Human Signoff Barrier Invariant Badge' },
  { id: 'badge-atomic-adoption', desc: 'INV-015 Atomic Adoption Invariant Badge' },
  { id: 'card-active-config', desc: 'Active Production Configuration Contract Card' },
  { id: 'active-version-badge', desc: 'Active Version & Approval Status Badge' },
  { id: 'active-approver', desc: 'Active Human Approver Identity Field' },
  { id: 'active-checksum', desc: 'Active Configuration Contract Checksum' },
  { id: 'config-yaml', desc: 'Raw Production YAML Viewer' },
  { id: 'card-candidate-proposals', desc: 'Candidate Research Proposals Card' },
  { id: 'proposals-list', desc: 'Candidate Proposals Interactive List' },
  { id: 'proposal-modal', desc: 'Formulate Candidate Proposal Slide/Modal' },
  { id: 'new-prop-id', desc: 'New Proposal ID Input Field' },
  { id: 'new-prop-hyp', desc: 'New Proposal Hypothesis Input Field' },
  { id: 'new-prop-params', desc: 'New Proposal Strategy Parameters JSON Textarea' },
  { id: 'card-metric-sharpe', desc: 'Sharpe Ratio Delta Comparison Card' },
  { id: 'card-metric-calmar', desc: 'Calmar Ratio Delta Comparison Card' },
  { id: 'card-metric-drawdown', desc: 'Max Drawdown Delta Comparison Card' },
  { id: 'card-metric-winrate', desc: 'Win Rate Delta Comparison Card' },
  { id: 'card-metric-expectancy', desc: 'Expectancy R Delta Comparison Card' },
  { id: 'workbench-equity-chart', desc: 'Comparative Backtest Trajectory Container' },
  { id: 'svg-equity-curves', desc: 'SVG Equity Curve Graphic Element' },
  { id: 'polyline-baseline', desc: 'Baseline Equity Polyline' },
  { id: 'polyline-proposed', desc: 'Candidate Variant Polyline' },
  { id: 'workbench-yaml-diff-container', desc: 'Side-by-Side YAML Diff Viewer Container' },
  { id: 'yaml-diff-view', desc: 'Unified YAML Diff Code Preformatted Block' },
  { id: 'proposal-approval-panel', desc: 'Cryptographic Human Signoff & Adoption Gate' },
  { id: 'input-scientist-approver', desc: 'Scientist Human Approver ID Input' },
  { id: 'input-scientist-salt', desc: 'Scientist Authorization Secret Salt Input' },
  { id: 'input-new-version', desc: 'Production Target Adoption Version Input' },
  { id: 'btn-approve-proposal', desc: 'Cryptographically Approve Proposal Action Button' },
  { id: 'btn-adopt-proposal', desc: 'Adopt into Production Config Action Button' },
  { id: 'btn-reject-proposal', desc: 'Reject Proposal Action Button' },
  { id: 'proposal-action-feedback', desc: 'Dual-Control Cryptographic Seal Feedback Container' },
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

// Invariants and endpoint integration checks
const requiredWiring = [
  { needle: '/api/scientist/config', desc: 'Scientist Studio Config Endpoint' },
  { needle: '/api/scientist/proposals', desc: 'Scientist Studio Proposals Endpoint' },
  { needle: '/approve', desc: 'Cryptographic Proposal Approval Action API' },
  { needle: '/adopt', desc: 'Atomic Production Config Adoption Action API' },
  { needle: 'INV-014', desc: 'Cryptographic Human Signoff Barrier Invariant Citation' },
  { needle: 'INV-015', desc: 'Atomic Configuration Adoption Guarantee Invariant Citation' },
  { needle: 'fetchScientistWorkbench', desc: 'Workbench Fetch & Reconciliation Engine' },
  { needle: 'handleApproveProposal', desc: 'Cryptographic Approval Handler' },
  { needle: 'handleAdoptProposal', desc: 'Atomic Production Adoption Handler' },
];

for (const wire of requiredWiring) {
  if (html.includes(wire.needle)) {
    console.log(`[PASS] Verified Wiring / Invariant: ${wire.desc}`);
  } else {
    console.error(`[FAIL] Missing Wiring / Invariant: ${wire.desc}`);
    failed++;
  }
}

if (failed > 0) {
  console.error(`UI-004 Scientist Studio Audit failed with ${failed} issues.`);
  process.exit(1);
}

console.log('All UI-004 Scientist Studio & Proposal Diff Workbench assertions PASSED successfully.');
process.exit(0);
