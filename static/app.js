'use strict';

const $ = id => document.getElementById(id);

const levels = {
  easy: 'Лёгкая',
  medium: 'Средняя',
  hard: 'Сложная'
};

let csrf = '';
let user = null;
let tasks = [];
let profile = null;
let filter = 'all';
let subject = 'Алгебра';
let register = false;
let selectedId = null;
let navigationVersion = 0;
let dialogVersion = 0;

let historySubject = 'Алгебра';
let historyItems = [];
let historyTaskSubjects = new Map();

function el(tag, cls, value) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (value !== undefined) node.textContent = value;
  return node;
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;

  const dark = theme === 'dark';

  $('theme-icon').textContent = dark ? '☀' : '☾';
  $('theme-label').textContent = dark ? 'Светлая тема' : 'Тёмная тема';

  $('theme-toggle').setAttribute(
    'aria-label',
    dark ? 'Включить светлую тему' : 'Включить тёмную тему'
  );

  $('theme-toggle').setAttribute('aria-pressed', String(dark));

  try {
    localStorage.setItem('olymp-theme', theme);
  } catch {
    // Тема работает и без доступа к localStorage.
  }
}

let savedTheme;

try {
  savedTheme = localStorage.getItem('olymp-theme');
} catch {
  // При недоступном хранилище используем тему системы.
}

setTheme(
  ['light', 'dark'].includes(savedTheme)
    ? savedTheme
    : matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light'
);

$('theme-toggle').onclick = () => {
  setTheme(
    document.documentElement.dataset.theme === 'dark'
      ? 'light'
      : 'dark'
  );
};

async function api(path, data) {
  const options = {
    credentials: 'same-origin',
    headers: {}
  };

  if (data !== undefined) {
    options.method = 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-CSRF-Token'] = csrf;
    options.body = JSON.stringify(data);
  }

  let response;

  try {
    response = await fetch('/api/' + path, options);
  } catch {
    throw new Error(
      'Не удалось связаться с сервером. Проверь, что он запущен.'
    );
  }

  const result = await response.json();

  if (!response.ok) {
    if (
      response.status === 401 &&
      !['login', 'register'].includes(path)
    ) {
      user = null;
      $('task-dialog').close();
      showAuth();
    }

    throw new Error(result.error || 'Не удалось выполнить запрос.');
  }

  return result;
}

function showAuth() {
  $('auth-view').hidden = false;
  $('app-view').hidden = true;

  historySubject = 'Алгебра';
  historyItems = [];
  historyTaskSubjects = new Map();

  $('history-table').replaceChildren();

  const tabs = $('history-subject-tabs');
  if (tabs) tabs.remove();
}

function authMode(value) {
  register = value;

  $('register-tab').classList.toggle('active', value);
  $('login-tab').classList.toggle('active', !value);

  $('auth-title').textContent = value
    ? 'Создай аккаунт'
    : 'С возвращением';

  $('auth-description').textContent = value
    ? 'Сохраняй решения и следи за результатами.'
    : 'Войди в свой аккаунт.';

  $('auth-submit').replaceChildren(
    document.createTextNode(value ? 'Зарегистрироваться' : 'Войти'),
    el('span', '', '↗')
  );

  $('password').autocomplete = value
    ? 'new-password'
    : 'current-password';

  $('auth-error').textContent = '';
}

$('login-tab').onclick = () => authMode(false);
$('register-tab').onclick = () => authMode(true);

$('auth-form').onsubmit = async event => {
  event.preventDefault();

  $('auth-error').textContent = '';
  $('auth-submit').disabled = true;

  try {
    const result = await api(register ? 'register' : 'login', {
      username: $('username').value,
      password: $('password').value
    });

    user = result.user;
    csrf = result.csrf;
    $('password').value = '';

    await enter();
  } catch (error) {
    $('auth-error').textContent = error.message;
  } finally {
    $('auth-submit').disabled = false;
  }
};

async function enter() {
  $('sidebar-name').textContent = user.username;
  $('avatar').textContent = user.username[0].toUpperCase();
  $('profile-avatar').textContent = user.username[0].toUpperCase();
  $('profile-title').textContent = user.username;

  filter = 'all';
  $('task-search').value = '';

  await refresh();

  $('auth-view').hidden = true;
  $('app-view').hidden = false;

  await navigate('tasks', 'Алгебра');
}

async function refresh() {
  const account = user.id;

  const result = await Promise.all([
    api('tasks'),
    api('profile')
  ]);

  if (!user || user.id !== account) return;

  [tasks, profile] = result;

  renderTasks();
  renderProfile();
}

function normalize(value) {
  return value.toLocaleLowerCase('ru-RU').replaceAll('ё', 'е');
}

function renderTasks() {
  const all = tasks.filter(task => task.subject === subject);
  const solved = all.filter(task => task.solved).length;

  const percent = all.length
    ? Math.round(solved / all.length * 100)
    : 0;

  $('stat-solved').textContent = solved;
  $('stat-total').textContent = all.length;
  $('stat-points').textContent = profile.points;
  $('stat-progress').textContent = percent + '%';
  $('progress-fill').style.width = percent + '%';

  const words = normalize($('task-search').value)
    .trim()
    .split(/\s+/)
    .filter(Boolean);

  const list = all.filter(task => {
    const aliases = {
      easy: 'легкие easy',
      medium: 'средние medium',
      hard: 'сложные hard'
    };

    const text = normalize(
      task.title + ' ' +
      levels[task.difficulty] + ' ' +
      aliases[task.difficulty]
    );

    return (
      (filter === 'all' || task.difficulty === filter) &&
      words.every(word => text.includes(word))
    );
  });

  $('task-count').textContent = list.length + ' / ' + all.length;
  $('task-list').replaceChildren();

  document.querySelectorAll('[data-filter]').forEach(button => {
    const active = button.dataset.filter === filter;

    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });

  for (const task of list) {
    const row = el(
      'button',
      'task-row' + (task.solved ? ' solved' : '')
    );

    row.type = 'button';

    row.setAttribute(
      'aria-label',
      'Задача ' + task.number + '. ' + task.title
    );

    row.onclick = () => openTask(task.id);

    const copy = el('span', 'task-copy');

    copy.append(
      el('span', 'task-title', task.title),
      el('span', 'task-preview', task.statement)
    );

    const state = task.awarded
      ? '✓ Решено · +1 балл' +
        (task.revealed
          ? ' · разбор просмотрен после решения'
          : '')
      : task.revealed
        ? (
          task.solved
            ? '✓ Решено · разбор открыт заранее · без балла'
            : 'Разбор просмотрен · без балла'
        )
        : '+1 балл';

    copy.append(el(
      'span',
      'task-state' + (task.awarded ? ' scored' : ''),
      state
    ));

    row.append(
      el('span', 'task-index', task.number),
      copy,
      el(
        'span',
        'badge ' + task.difficulty,
        levels[task.difficulty]
      ),
      el('span', 'row-arrow', '↗')
    );

    $('task-list').append(row);
  }

  if (!list.length) {
    $('task-list').append(el(
      'div',
      'empty',
      'Ничего не найдено. Измени запрос или выбери другую сложность.'
    ));
  }
}

$('task-search').oninput = renderTasks;

$('filters').onclick = event => {
  const button = event.target.closest('button[data-filter]');
  if (!button) return;

  filter = button.dataset.filter;
  renderTasks();
};

function renderProfile() {
  $('profile-stats').replaceChildren();

  const stats = [
    ['Решено задач', profile.solved_count],
    ['Набрано баллов', profile.points],
    ['Всего попыток', profile.attempt_count],
    ['Открыто разборов', profile.revealed_count]
  ];

  for (const [label, value] of stats) {
    const box = el('div');

    box.append(
      el('strong', '', value),
      el('span', '', label)
    );

    $('profile-stats').append(box);
  }

  $('solved-list').replaceChildren();

  for (const task of profile.solved) {
    const row = el(
      'div',
      'solved-row' + (task.awarded ? '' : ' no-points')
    );

    row.append(
      el('strong', '', task.title),
      el(
        'span',
        '',
        task.awarded
          ? '✓ Самостоятельно · +1 балл'
          : '✓ Решено · разбор просмотрен · 0 баллов'
      )
    );

    $('solved-list').append(row);
  }

  if (!profile.solved.length) {
    $('solved-list').append(el(
      'div',
      'empty',
      'Пока нет решённых задач. Выбери олимпиаду в меню.'
    ));
  }
}

function table(container, headers, rows) {
  const target = $(container);
  target.replaceChildren();

  if (!rows.length) {
    target.append(el('div', 'empty', 'Пока нет записей.'));
    return;
  }

  const tab = el('table');
  const head = el('thead');
  const tr = el('tr');
  const body = el('tbody');

  headers.forEach(text => {
    const th = el('th', '', text);
    th.scope = 'col';
    tr.append(th);
  });

  head.append(tr);
  rows.forEach(row => body.append(row));
  tab.append(head, body);
  target.append(tab);
}

function date(value) {
  return new Date(value).toLocaleString('ru-RU', {
    dateStyle: 'short',
    timeStyle: 'short'
  });
}

function renderHistory() {
  let tabs = $('history-subject-tabs');

  if (!tabs) {
    tabs = el('div', 'subject-tabs');
    tabs.id = 'history-subject-tabs';

    tabs.setAttribute('role', 'group');
    tabs.setAttribute(
      'aria-label',
      'Предмет в истории попыток'
    );

    tabs.style.justifyContent = 'center';

    $('history-table').before(tabs);

    for (const name of ['Алгебра', 'Информатика']) {
      const button = el('button');

      button.type = 'button';
      button.dataset.historySubject = name;

      button.onclick = () => {
        historySubject = name;
        renderHistory();
      };

      tabs.append(button);
    }
  }

  tabs.querySelectorAll('button').forEach(button => {
    const name = button.dataset.historySubject;

    const count = historyItems.filter(attempt => {
      return historyTaskSubjects.get(attempt.task_id) === name;
    }).length;

    const active = name === historySubject;

    button.textContent = name + ' · ' + count;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });

  const visible = historyItems.filter(attempt => {
    return (
      historyTaskSubjects.get(attempt.task_id) === historySubject
    );
  });

  $('history-table').setAttribute('aria-live', 'polite');

  table(
    'history-table',
    [
      'Задача',
      'Твой ответ',
      'Результат',
      'Ответ и разбор',
      'Баллы',
      'Дата'
    ],
    visible.map(attempt => {
      const viewed = !attempt.revealed
        ? 'Не просмотрен'
        : attempt.revealed_before_attempt
          ? 'Просмотрен до этой попытки'
          : 'Просмотрен после этой попытки';

      const row = el('tr');

      row.append(
        el('td', '', attempt.title),
        el('td', 'answer-cell', attempt.answer),
        el(
          'td',
          attempt.correct ? 'status-ok' : 'status-no',
          attempt.correct ? '✓ Верно' : '× Неверно'
        ),
        el('td', '', viewed),
        el(
          'td',
          attempt.awarded ? 'status-ok' : '',
          attempt.awarded ? '+1' : '0'
        ),
        el('td', '', date(attempt.created_at))
      );

      return row;
    })
  );

  if (!visible.length) {
    const message = historySubject === 'Алгебра'
      ? 'Попыток по алгебре пока нет.'
      : 'Попыток по информатике пока нет.';

    $('history-table').replaceChildren(
      el('div', 'empty', message)
    );
  }
}

async function navigate(page, newSubject) {
  const version = ++navigationVersion;

  if (newSubject && newSubject !== subject) {
    subject = newSubject;
    filter = 'all';
    $('task-search').value = '';
  }

  $('global-error').textContent = '';

  document.querySelectorAll('[id^="page-"]').forEach(section => {
    section.hidden = section.id !== 'page-' + page;
  });

  document.querySelectorAll('.nav-item').forEach(button => {
    button.classList.toggle(
      'active',
      button.dataset.page === page &&
      (
        !button.dataset.subject ||
        button.dataset.subject === subject
      )
    );
  });

  document.querySelectorAll(
    '.subject-tabs [data-subject]'
  ).forEach(button => {
    const active = button.dataset.subject === subject;

    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });

  $('profile-open').classList.toggle(
    'active',
    page === 'profile'
  );

  $('breadcrumb-current').textContent = {
    tasks: subject,
    profile: 'Личный кабинет',
    history: 'История попыток',
    leaderboard: 'Рейтинг'
  }[page];

  $('catalog-title').replaceChildren(
    document.createTextNode('Олимпиада'),
    el('br'),
    document.createTextNode(
      subject === 'Алгебра'
        ? 'по алгебре'
        : 'по информатике'
    ),
    el('span', '', '.')
  );

  $('catalog-kicker').textContent = subject === 'Алгебра'
    ? 'МАТЕМАТИКА / 01'
    : 'АЛГОРИТМЫ / 02';

  $('catalog-description').textContent = subject === 'Алгебра'
    ? 'От простых уравнений к неожиданным закономерностям.'
    : 'Логика, алгоритмы и задачи, которые учат думать.';

  $('graphic-symbol').textContent = subject === 'Алгебра'
    ? 'x²'
    : '{ }';

  try {
    if (page === 'profile' || page === 'tasks') {
      await refresh();
    }

    if (page === 'history') {
      const [history, catalog] = await Promise.all([
        api('history'),
        api('tasks')
      ]);

      if (version !== navigationVersion || !user) return;

      historyItems = history;
      historyTaskSubjects = new Map(
        catalog.map(task => [task.id, task.subject])
      );

      renderHistory();
    }

    if (page === 'leaderboard') {
      const leaders = await api('leaderboard');

      if (version !== navigationVersion || !user) return;

      table(
        'leaderboard-table',
        ['Место', 'Участник', 'Баллы'],
        leaders.map((leader, index) => {
          const row = el(
            'tr',
            leader.id === user.id ? 'you' : ''
          );

          row.append(
            el('td', '', String(index + 1).padStart(2, '0')),
            el(
              'td',
              '',
              leader.username +
              (leader.id === user.id ? ' · это ты' : '')
            ),
            el('td', '', leader.points)
          );

          return row;
        })
      );
    }
  } catch (error) {
    if (version === navigationVersion) {
      $('global-error').textContent = error.message;
    }
  }
}

document.querySelectorAll('[data-page]').forEach(button => {
  button.onclick = () => navigate(
    button.dataset.page,
    button.dataset.subject
  );
});

document.querySelectorAll(
  '.subject-tabs [data-subject]'
).forEach(button => {
  button.onclick = () => navigate(
    'tasks',
    button.dataset.subject
  );
});

$('profile-open').onclick = () => navigate('profile');

function scoreNote(task) {
  $('task-score-note').textContent = task.awarded
    ? 'Балл уже начислен и сохранится после просмотра разбора.'
    : task.revealed
      ? 'Разбор открыт до первого верного ответа. Балл за задачу недоступен.'
      : 'За самостоятельное решение — 1 балл.';

  $('solution-note').textContent = task.awarded
    ? 'Разбор просмотрен после решения. Заработанный балл сохранён.'
    : 'Разбор просмотрен до первого верного ответа. Решать можно для практики, без балла.';
}

async function openTask(taskId) {
  const version = ++dialogVersion;
  selectedId = taskId;

  const task = tasks.find(t => t.id === taskId);

  $('dialog-title').textContent = task.title;
  $('dialog-statement').textContent = task.statement;
  $('dialog-subject').textContent =
    task.subject + ' / ЗАДАЧА ' + task.number;

  $('dialog-badge').className = 'badge ' + task.difficulty;
  $('dialog-badge').textContent = levels[task.difficulty];

  $('answer').value = '';
  $('answer-result').textContent = '';
  $('solution-error').textContent = '';
  $('solution-content').hidden = true;
  $('reveal-confirm').hidden = true;
  $('solution-answer').textContent = '';
  $('solution-explanation').textContent = '';
  $('reveal-open').disabled = false;

  scoreNote(task);

  $('task-dialog').showModal();
  $('task-dialog').scrollTop = 0;
  $('answer').focus();

  if (task.revealed) {
    await revealTask(version);
  }
}

$('close-dialog').onclick = () => $('task-dialog').close();

$('task-dialog').addEventListener('close', () => {
  dialogVersion++;
});

const taskDialog = $('task-dialog');

function outsideDialog(event) {
  const rect = taskDialog.getBoundingClientRect();

  return (
    event.clientX < rect.left ||
    event.clientX > rect.right ||
    event.clientY < rect.top ||
    event.clientY > rect.bottom
  );
}

taskDialog.addEventListener('click', event => {
  if (
    event.target === taskDialog &&
    outsideDialog(event)
  ) {
    taskDialog.close();
  }
});

function setBusy(value) {
  $('answer-submit').disabled = value;
  $('reveal-open').disabled = value;
  $('reveal-confirm-button').disabled = value;
}

$('answer-form').onsubmit = async event => {
  event.preventDefault();

  const version = dialogVersion;
  const taskId = selectedId;

  setBusy(true);
  $('answer-result').textContent = '';

  try {
    const result = await api('submit', {
      task_id: taskId,
      answer: $('answer').value
    });

    if (version === dialogVersion) {
      $('answer-result').className = 'result ' +
        (result.correct ? 'status-ok' : 'status-no');

      $('answer-result').textContent = result.correct
        ? result.awarded
          ? 'Верно! +1 балл.'
          : !result.eligible
            ? 'Верно! Разбор уже просмотрен — решение сохранено без балла.'
            : 'Верно! Задача уже решена, дополнительный балл не начисляется.'
        : 'Неверно. Попытка сохранена. Попробуй ещё раз.';
    }

    await refresh();

    if (version === dialogVersion) {
      scoreNote(tasks.find(t => t.id === taskId));
    }
  } catch (error) {
    if (version === dialogVersion) {
      $('answer-result').className = 'result status-no';
      $('answer-result').textContent = error.message;
    }
  } finally {
    setBusy(false);
  }
};

$('reveal-open').onclick = () => {
  const task = tasks.find(t => t.id === selectedId);

  if (task.revealed || task.awarded) {
    revealTask(dialogVersion);
  } else {
    $('reveal-confirm').hidden = false;
  }
};

$('reveal-cancel').onclick = () => {
  $('reveal-confirm').hidden = true;
};

$('reveal-confirm-button').onclick = () => {
  revealTask(dialogVersion);
};

async function revealTask(version) {
  const taskId = selectedId;

  setBusy(true);
  $('solution-error').textContent = '';

  try {
    const result = await api('reveal', {
      task_id: taskId
    });

    if (version === dialogVersion) {
      const task = tasks.find(t => t.id === taskId);
      task.revealed = 1;

      scoreNote(task);

      $('reveal-confirm').hidden = true;
      $('solution-answer').textContent = result.answer;
      $('solution-explanation').textContent = result.explanation;
      $('solution-content').hidden = false;
      $('answer-result').textContent = '';
    }

    try {
      await refresh();
    } catch {
      $('global-error').textContent =
        'Просмотр сохранён. Обнови страницу для обновления прогресса.';
    }
  } catch (error) {
    if (version === dialogVersion) {
      $('solution-error').textContent = error.message;
    }
  } finally {
    setBusy(false);
  }
}

$('logout').onclick = async () => {
  $('logout').disabled = true;

  try {
    const result = await api('logout', {});

    csrf = result.csrf;
    user = null;
    tasks = [];
    profile = null;
    navigationVersion++;

    $('task-dialog').close();
    showAuth();
  } catch (error) {
    $('global-error').textContent = error.message;
  } finally {
    $('logout').disabled = false;
  }
};

(async () => {
  try {
    const session = await api('session');
    csrf = session.csrf;
    user = session.user;

    if (user) {
      await enter();
    } else {
      showAuth();
    }
  } catch (error) {
    $('auth-error').textContent = error.message;
  }
})();