const LONG_TERM_CATEGORIES = [
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

const SHORT_TERM_CATEGORIES = [
  'active_task',
  'pending_questions',
  'recent_entities',
  'emotional_context',
  'general', // Short term also has general summary
];

const CATEGORY_LABELS = {
  // Long Term
  profile: '基本情報',
  preference: '好み・嗜好',
  health: '健康',
  work: '仕事・学業',
  hobby: '趣味',
  relationship: '人間関係',
  life: '生活',
  travel: '旅行',
  food: '食事',
  general: 'その他・メモ',

  // Short Term
  active_task: '現在進行中のタスク',
  pending_questions: '未解決の質問',
  recent_entities: '直近の話題・キーワード',
  emotional_context: '現在の感情・雰囲気',
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
  general: '例: 雑多なメモや、まだ分類できていない情報。',

  active_task: '例: タスク: 旅行の計画を立てる (ステータス: 進行中)',
  pending_questions: '例: 質問: 次回の会議はいつ？\n質問: あのレストランの名前は？',
  recent_entities: '例: キーワード: React, Python, 温泉',
  emotional_context: '例: 気分: 落ち着いている。少し急ぎ。',
};

/**
 * Convert structured short-term data into natural language text for the editor.
 */
function formatShortTermValue(category, data, fullMemory) {
  // If we already have a text summary for this category, prefer it (unless it's empty)
  // However, for structured fields like active_task, we want to reconstruct the text from the structure if possible,
  // to ensure the user sees the actual state.
  
  if (category === 'active_task') {
    const task = fullMemory.active_task || {};
    if (task.goal) {
      return `タスク: ${task.goal}\nステータス: ${task.status || 'active'}`;
    }
  }

  if (category === 'pending_questions') {
    const questions = fullMemory.pending_questions || [];
    if (Array.isArray(questions) && questions.length > 0) {
      return questions.map(q => `質問: ${q}`).join('\n');
    }
  }

  if (category === 'recent_entities') {
    const entities = fullMemory.recent_entities || [];
    if (Array.isArray(entities) && entities.length > 0) {
      // entities are dicts {name: "...", ...}
      const names = entities.map(e => e.name).filter(n => n);
      if (names.length > 0) {
        return `キーワード: ${names.join(', ')}`;
      }
    }
  }

  if (category === 'emotional_context') {
    if (fullMemory.emotional_context) {
      return `気分: ${fullMemory.emotional_context}`;
    }
  }

  // Fallback to existing category summary string if available
  if (data && typeof data === 'string') {
    return data;
  }

  return '';
}

function buildSections(containerId, categoryList, summaries = {}, fullMemory = {}) {
  const container = document.getElementById(containerId);
  container.innerHTML = '';

  categoryList.forEach((cat) => {
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

    // Determine initial value
    let value = '';
    // For short-term specific structured fields, try to format them
    if (SHORT_TERM_CATEGORIES.includes(cat) && cat !== 'general') {
       value = formatShortTermValue(cat, summaries[cat], fullMemory);
    } else {
       value = summaries[cat] || '';
    }
    
    textarea.value = value;

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
      // Build Long Term (Standard Categories)
      buildSections(longContainer, LONG_TERM_CATEGORIES, data.long_term_categories, data.long_term_full);
      
      // Build Short Term (Special Categories)
      buildSections(shortContainer, SHORT_TERM_CATEGORIES, data.short_term_categories, data.short_term_full);
    })
    .catch((error) => {
      console.error('Error fetching memory:', error);
      statusMessage.textContent = 'メモリの読み込みに失敗しました。';
    });

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    statusMessage.textContent = '保存中...';

    const longTermData = collectSections(longContainer);
    const shortTermData = collectSections(shortContainer);

    fetch('/api/memory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        // We send "categories" map. The backend `replace_with_user_payload` expects this structure
        // or a simple map which it treats as categories.
        long_term_memory: longTermData, 
        short_term_memory: shortTermData,
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