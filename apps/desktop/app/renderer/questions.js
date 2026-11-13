'use strict';

const ANSWER_PAUSE_MS = 1000;
const NEW_QUESTION = 'New question';
const ANSWER_WAITING_CLASS = 'answer-waiting';
const STEP_ANSWERS = [['Allow', true], ['Not now', false]];
const TASK_LABELS = { approve: 'Approve', deny: 'Deny', done: 'Done', cancel: 'Cancel task' };

function answerGuard(clock = () => Date.now()) {
  let seenAt = null;
  return {
    see() {
      if (seenAt === null) seenAt = clock();
    },
    isReady() {
      return seenAt !== null && clock() - seenAt >= ANSWER_PAUSE_MS;
    },
  };
}

function whenSeen(element, onSeen) {
  if (typeof IntersectionObserver !== 'function') {
    onSeen();
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) return;
    observer.disconnect();
    onSeen();
  });
  observer.observe(element);
}

function flashNewQuestion(card, buttons, guard) {
  const flash = document.createElement('span');
  flash.className = 'new-question';
  flash.setAttribute('role', 'status');
  flash.textContent = NEW_QUESTION;
  card.classList.add(ANSWER_WAITING_CLASS);
  card.append(flash);
  buttons.forEach((button) => button.setAttribute('aria-disabled', 'true'));
  whenSeen(card, () => {
    guard.see();
    setTimeout(() => {
      card.classList.remove(ANSWER_WAITING_CLASS);
      flash.remove();
      buttons.forEach((button) => button.removeAttribute('aria-disabled'));
    }, ANSWER_PAUSE_MS);
  });
  return card;
}

function answerButton(label, guard, onPress) {
  const button = document.createElement('button');
  button.textContent = label;
  button.addEventListener('click', () => {
    if (guard.isReady()) onPress();
  });
  return button;
}

function stepQuestionCard(text, onAnswer) {
  const card = document.createElement('div');
  card.className = 'ask';
  const question = document.createElement('p');
  question.textContent = text;
  const actions = document.createElement('div');
  actions.className = 'actions';
  const guard = answerGuard();
  const buttons = STEP_ANSWERS.map(([label, ok]) => {
    const button = answerButton(label, guard, () => {
      card.classList.add('done');
      actions.remove();
      question.textContent += ok ? '  (allowed)' : '  (not allowed)';
      onAnswer(ok);
    });
    if (ok) button.className = 'allow';
    return button;
  });
  actions.append(...buttons);
  card.append(question, actions);
  return flashNewQuestion(card, buttons, guard);
}

function taskAnswerCard(actions, onAction) {
  const card = document.createElement('div');
  card.className = 'ask';
  const row = document.createElement('div');
  row.className = 'actions';
  const guard = answerGuard();
  const buttons = actions.filter((action) => TASK_LABELS[action]).map((action) => {
    const button = answerButton(TASK_LABELS[action], guard, () => {
      row.remove();
      card.classList.add('done');
      const result = document.createElement('p');
      result.textContent = '…';
      card.append(result);
      onAction(action, result);
    });
    if (action === 'approve' || action === 'done') button.className = 'allow';
    return button;
  });
  row.append(...buttons);
  card.append(row);
  return flashNewQuestion(card, buttons, guard);
}
