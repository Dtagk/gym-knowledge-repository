let activeMuscle = null;
let activeTab    = 'training';

// ── View toggle ──────────────────────────────────────────────
document.querySelectorAll('.view-toggle button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.view-toggle button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const isFront = btn.dataset.view === 'front';
    document.getElementById('front-view').classList.toggle('hidden', !isFront);
    document.getElementById('back-view').classList.toggle('hidden',  isFront);
  });
});

// ── Muscle hover label ───────────────────────────────────────
const hoverLabel = document.getElementById('hover-label');

document.querySelectorAll('.muscle').forEach(g => {
  g.addEventListener('mouseenter', () => {
    const data = muscleData[g.dataset.muscle];
    hoverLabel.textContent = data ? data.name : '';
  });
  g.addEventListener('mouseleave', () => {
    hoverLabel.innerHTML = '&nbsp;';
  });
});

// ── Muscle click ─────────────────────────────────────────────
document.querySelectorAll('.muscle').forEach(g => {
  g.addEventListener('click', () => {
    activeMuscle = g.dataset.muscle;
    activeTab    = 'training';

    // sync active state across both SVGs
    document.querySelectorAll('.muscle').forEach(el => {
      el.classList.toggle('active', el.dataset.muscle === activeMuscle);
    });

    // reset tab highlight
    document.querySelectorAll('.tab').forEach(t => {
      t.classList.toggle('active', t.dataset.tab === activeTab);
    });

    renderPanel();
  });
});

// ── Tab click ────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    if (!activeMuscle) return;
    activeTab = tab.dataset.tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === tab));
    renderContent();
  });
});

// ── Render ───────────────────────────────────────────────────
function renderPanel() {
  const data = muscleData[activeMuscle];
  if (!data) return;

  document.getElementById('detail-empty').style.display   = 'none';
  document.getElementById('detail-content').style.display = 'block';
  document.getElementById('muscle-title').textContent     = data.name;
  renderContent();
}

function renderContent() {
  const data    = muscleData[activeMuscle];
  const section = data[activeTab];
  const panel   = document.getElementById('tab-panel');
  panel.textContent = '';

  if (!section.sections || section.sections.length === 0) {
    const msg = document.createElement('div');
    msg.className = 'empty-state';
    msg.append(
      'No content yet — add entries to ',
      Object.assign(document.createElement('code'), { textContent: 'data.js' }),
      ' under ',
      Object.assign(document.createElement('code'), { textContent: `${activeMuscle}.${activeTab}.sections` }),
      '.',
    );
    panel.appendChild(msg);
    return;
  }

  section.sections.forEach(s => {
    const div = document.createElement('div');
    div.className = 'content-section';

    if (s.title) {
      div.appendChild(Object.assign(document.createElement('h3'), { textContent: s.title }));
    }

    const ul = document.createElement('ul');
    s.items.forEach(item => {
      ul.appendChild(Object.assign(document.createElement('li'), { textContent: item }));
    });
    div.appendChild(ul);
    panel.appendChild(div);
  });
}
