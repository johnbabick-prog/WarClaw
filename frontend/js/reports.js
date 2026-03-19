/**
 * WarClaw — Reports Module
 */

function renderReport(result) {
  const report = result.report;
  const target = document.getElementById('report-output');
  target.innerHTML = `
    <h2>${report.title}</h2>
    <p>${report.summary}</p>
    ${(report.sections || []).map(section => `
      <div style="margin-top:18px;">
        <h3>${section.heading}</h3>
        <p>${section.body}</p>
      </div>
    `).join('')}
    <div style="margin-top:18px;">
      <h3>Recommended Actions</h3>
      <ul>
        ${(report.recommended_actions || []).map(item => `<li>${item}</li>`).join('')}
      </ul>
    </div>
  `;
}

async function loadReportsView() {
  const data = await API.get('/api/reports/templates');
  State.reportTemplates = data.templates;
  const select = document.getElementById('report-template-select');
  select.innerHTML = data.templates.map(tpl => `
    <option value="${tpl.id}">${tpl.name}</option>
  `).join('');
}

async function generateReport() {
  const reportType = document.getElementById('report-template-select').value;
  const focus = document.getElementById('report-focus-input').value.trim();
  const progress = createProgressController('report-progress-fill', 'report-status', 'report-eta', {
    message: 'Collecting mission log, agent posture, and generated app context...',
    etaSeconds: 20,
    intervalMs: 850,
    step: 4,
  });

  try {
    const result = await API.post('/api/reports/generate', {
      report_type: reportType,
      focus,
    });
    renderReport(result);
    progress.complete(`Generated ${result.report.title}`);
  } catch (e) {
    progress.fail(e.message);
  } finally {
    progress.reset();
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('report-generate-btn');
  if (btn) btn.onclick = generateReport;
  loadReportsView().catch(() => {});
});
