const MEMORY_CATEGORIES = [
  'profile',
  'preference',
  'health',
  'work',
  'hobby',
  'relationship',
  'life',
  'travel',
  'food',
  'general',
];

const CATEGORY_LABELS = {
  profile: '基本情報',
  preference: '好み・嗜好',
  health: '健康',
  work: '仕事・学業',
  hobby: '趣味',
  relationship: '人間関係',
  life: '生活',
  travel: '旅行',
  food: '食事',
  general: 'その他',
};

const PLACEHOLDER = {
  profile: '例: 名前は山田太郎。東京在住。30代。エンジニアとして働いている。',
  preference: '例: 返答は簡潔が好き。敬体が好み。長文より箇条書きが助かる。',
  health: '例: 毎日朝にジョギング。カフェイン控えめを希望。',
  work: '例: プロジェクトXの締切は毎週金曜。リモート勤務中心。',
  hobby: '例: ロードバイクと写真が趣味。休日は多摩川沿いを走る。',
  relationship: '例: 佐藤さんとは同僚。田中さんはメンター。',
  life: '例: 早朝型。家事は週末にまとめて行う。',
  travel: '例: 夏に北海道旅行を計画中。温泉が好き。',
  food: '例: 和食とコーヒーが好き。辛すぎる料理は苦手。',
  general: '例: 今日は穏やかな気分。次に話すときのトピック候補はAIとDIY。',
};

function buildSections(containerId, categoryData = {}, fallbackSummary = '') {
  const container = document.getElementById(containerId);
  container.innerHTML = '';

  const seeded = { ...categoryData };
  if (fallbackSummary && Object.keys(seeded).length === 0) {
    seeded.general = fallbackSummary;
  }

  MEMORY_CATEGORIES.forEach((cat) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'memory-card';

    const label = document.createElement('label');
    label.setAttribute('for', `${containerId}-${cat}`);
    label.textContent = CATEGORY_LABELS[cat] || cat;

    const hint = document.createElement('div');
    hint.className = 'memory-hint';
    hint.textContent = PLACEHOLDER[cat] || '';

    const textarea = document.createElement('textarea');
    textarea.id = `${containerId}-${cat}`;
    textarea.dataset.category = cat;
    textarea.placeholder = PLACEHOLDER[cat] || '';
    textarea.value = seeded[cat] || '';

    wrapper.appendChild(label);
    wrapper.appendChild(hint);
    wrapper.appendChild(textarea);
    container.appendChild(wrapper);
  });
}

function collectSections(containerId) {
  const container = document.getElementById(containerId);
  const areas = container.querySelectorAll('textarea[data-category]');
  const result = {};
  areas.forEach((area) => {
    const text = area.value.trim();
    if (text) {
      result[area.dataset.category] = text;
    }
  });
  return result;
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('memoryForm');
  const statusMessage = document.getElementById('statusMessage');
  const longContainer = 'longTermSections';
  const shortContainer = 'shortTermSections';

  fetch('/api/memory')
    .then((response) => response.json())
    .then((data) => {
      buildSections(longContainer, data.long_term_categories, data.long_term_memory);
      buildSections(shortContainer, data.short_term_categories, data.short_term_memory);
    })
    .catch((error) => {
      console.error('Error fetching memory:', error);
      statusMessage.textContent = 'メモリの読み込みに失敗しました。';
    });

  form.addEventListener('submit', (event) => {
    event.preventDefault();

    const longTermCategories = collectSections(longContainer);
    const shortTermCategories = collectSections(shortContainer);

    fetch('/api/memory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        long_term_memory: longTermCategories,
        short_term_memory: shortTermCategories,
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        statusMessage.textContent = data.message || '保存しました。';
        setTimeout(() => {
          statusMessage.textContent = '';
        }, 3000);
      })
      .catch((error) => {
        console.error('Error saving memory:', error);
        statusMessage.textContent = 'メモリの保存に失敗しました。';
      });
  });
});
