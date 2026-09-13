const $ = (selector) => document.querySelector(selector);
const screens = { difficulty: $('#difficulty-screen'), character: $('#character-screen'), sight: $('#sight-screen') };
const transition = $('#transition');
const menuAudio = $('#menu-audio');
const peaceAudio = $('#peace-audio');
const voiceAudio = $('#voice-audio');
const musicToggle = $('#music-toggle');
const dialogue = $('#novel-dialogue');
const speaker = $('#speaker');
const dialogueText = $('#sight-title');
const jiahaoCharacter = $('#jiahao-character');
const chapter = $('#chapter-label');

// 新台词可添加 voice: 'resources/sound/...'；null 表示暂时无配音。
// 场景图片与专属配乐留空时，使用已有校园背景与 peace。
const scenes = {
  gate: { title: '第一幕 · 初来乍到', image: 'resources/scene/sight/1.jpg', music: null },
  yard: { title: '第二幕 · 操场集合', image: 'resources/scene/sight/yard_assembly.png', music: null },
  exercise: { title: '第三幕 · 少年起势', image: 'resources/scene/sight/wushu_practice.png', music: null },
};
const story = [
  { text: '多么好的上午啊！' },
  { text: '花儿在绽放。' },
  { text: '鸟儿在歌唱。' },
  { text: '新学期也开始了。' },
  { speaker: '我', text: '这就是高中吗……有点期待呢。' },
  { speaker: '我', text: '区区高中，不足挂齿，你豪王来了！', voice: 'resources/sound/human/male/school_open1.mp3' },
  { text: '从背后传来声音：' },
  { speaker: '？？？', text: 'Oi！小鬼，你刚才提到了豪王是吗？', voice: 'resources/sound/human/male/school_open2.mp3' },
  { event: 'jiahaoEntrance' },
  { speaker: '我', text: '你……也是来报到的？', voice: null },
  { speaker: '？？？', text: '嘉豪。嘉奖的嘉，豪迈的豪。先记住这个名字。', voice: null },
  { speaker: '我', text: '这学校报到之前，还要先报江湖名号吗？', voice: null },
  { text: '他把手从口袋里抽出来。我下意识站直了。', voice: null },
  { speaker: '嘉豪', text: '别紧张。你校服标签露出来了。', voice: null },
  { speaker: '我', text: '……谢谢。', voice: null },
  { speaker: '广播', text: '请高一年级全体同学到操场集合，准备进行武术操入门练习。', voice: null },
  { speaker: '我', text: '第一天就练武？原来高中真的是江湖。', voice: null },
  { speaker: '嘉豪', text: '走吧，大侠。先找到我们班，不然第一招就是迟到。', voice: null },
  { text: '我跟着嘉豪穿过人群。前方的口令声，渐渐清晰起来。', voice: null },
  { event: 'scene', scene: 'yard' },
  { text: '操场上，各班的队伍已经排开。我们赶在点名前站进了最后一排。', voice: null },
  { speaker: '体育老师', text: '同学们，前后左右留出距离。今天先熟悉动作，整齐比抢快重要。', voice: null },
  { speaker: '嘉豪', text: '听见没？刚才那股豪气，待会儿别全用在喊口号上。', voice: null },
  { speaker: '我', text: '放心，我已经准备好一鸣惊人了。', voice: null },
  { text: '嘉豪低头看了一眼我并得过分整齐的双脚，忍住了笑。', voice: null },
  { speaker: '体育老师', text: '先跟口令走一遍：预备、抱拳、起势。不会没关系，看示范，慢慢跟上。', voice: null },
  { speaker: '我', text: '原来第一课不是打败谁，是先别踩到旁边的人。', voice: null },
  { speaker: '嘉豪', text: '这就对了。跟上，我在旁边。', voice: null },
  { event: 'scene', scene: 'exercise' },
  { speaker: '体育老师', text: '全体都有——立正！', voice: null },
  { text: '操场渐渐安静下来。我收住笑，跟着队伍站好。', voice: null },
  { speaker: '体育老师', text: '抱拳，行礼！', voice: null },
  { text: '我学着示范抬起双手。身旁的嘉豪也认真地行了一礼。', voice: null },
  { speaker: '体育老师', text: '武术操，预备——起势！', voice: null },
  { text: '一、二——随口令站稳，抬起双手。', voice: null },
  { text: '三、四——手臂缓缓落下，目视前方。', voice: null },
  { speaker: '我', text: '好像……也没有想象中那么难。', voice: null },
  { speaker: '嘉豪', text: '别分神。下一拍要来了。', voice: null },
  { speaker: '体育老师', text: '五、六，七、八！很好，保持队形！', voice: null },
  { text: '第一组动作结束。阳光落在整齐抬起的手臂上，我终于跟上了大家的节拍。', voice: null },
  { speaker: '我', text: '我的高中生活，就从这一拍开始吧。', voice: null },
];

let storyIndex = 0;
let typingTimer;
let isTyping = false;
let isSceneEventPlaying = false;
let currentMusic = menuAudio;
let muted = false;
let selectedCharacter = '男同学';
const pause = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

function showScreen(name) {
  Object.entries(screens).forEach(([key, screen]) => { screen.hidden = key !== name; });
}
function refreshMusicLabel() {
  musicToggle.textContent = muted ? '音乐：关' : currentMusic.paused ? '开启音乐' : '音乐：开';
  musicToggle.setAttribute('aria-pressed', String(!muted && !currentMusic.paused));
}
function tryMusic() {
  if (muted) return;
  currentMusic.play().then(refreshMusicLabel).catch(refreshMusicLabel);
}
function useMusic(audio) {
  if (currentMusic !== audio) {
    currentMusic.pause();
    currentMusic.currentTime = 0;
    currentMusic = audio;
  }
  currentMusic.volume = .32;
  currentMusic.muted = muted;
  tryMusic();
}
function stopVoice() {
  voiceAudio.pause();
  voiceAudio.removeAttribute('src');
  voiceAudio.load();
  currentMusic.volume = .32;
}
function playVoice(source) {
  stopVoice();
  if (!source) return;
  currentMusic.volume = .12;
  voiceAudio.src = source;
  voiceAudio.muted = muted;
  voiceAudio.play().catch(() => { currentMusic.volume = .32; });
}
function finishCurrentLine() {
  clearInterval(typingTimer);
  dialogueText.textContent = story[storyIndex].text;
  isTyping = false;
  dialogue.classList.remove('is-typing');
}
function renderLine() {
  const line = story[storyIndex];
  clearInterval(typingTimer);
  speaker.textContent = line.speaker || '旁白';
  dialogueText.textContent = '';
  dialogue.hidden = false;
  playVoice(line.voice);
  isTyping = true;
  dialogue.classList.add('is-typing');
  dialogue.classList.toggle('is-last', storyIndex === story.length - 1);
  let index = 0;
  typingTimer = setInterval(() => {
    dialogueText.textContent += line.text[index++];
    if (index >= line.text.length) finishCurrentLine();
  }, 46);
}
function setScene(name) {
  const scene = scenes[name];
  chapter.textContent = scene.title;
  const image = scene.image || 'resources/scene/physics/background_school.jpg';
  screens.sight.style.setProperty('--scene-image', 'url("' + image + '")');
  screens.sight.classList.toggle('is-still', name !== 'gate');
  if (scene.music) {
    peaceAudio.src = scene.music;
    useMusic(peaceAudio);
  }
}
async function playEvent(line) {
  isSceneEventPlaying = true;
  dialogue.hidden = true;
  stopVoice();
  if (line.event === 'jiahaoEntrance') {
    jiahaoCharacter.className = 'jiahao-character enter';
    await pause(1050);
    jiahaoCharacter.className = 'jiahao-character arrived';
    await pause(1150);
    jiahaoCharacter.classList.add('turn');
    await pause(405);
    jiahaoCharacter.classList.add('show-front');
    await pause(495);
    jiahaoCharacter.classList.remove('turn');
  } else {
    transition.classList.remove('play');
    void transition.offsetWidth;
    transition.classList.add('play');
    await pause(500);
    jiahaoCharacter.className = 'jiahao-character';
    setScene(line.scene);
    await pause(550);
  }
  isSceneEventPlaying = false;
  storyIndex += 1;
  if (storyIndex < story.length) renderLine();
}
function advanceStory() {
  if (screens.sight.hidden || isSceneEventPlaying) return;
  if (isTyping) { finishCurrentLine(); return; }
  // 有配音的句子说完后，才能继续；点击补全文字不会打断配音。
  if (!voiceAudio.paused && !voiceAudio.ended) return;
  if (storyIndex === story.length - 1) {
    stopVoice();
    jiahaoCharacter.className = 'jiahao-character';
    showScreen('difficulty');
    useMusic(menuAudio);
    $('#easy-button').focus();
    return;
  }
  storyIndex += 1;
  if (story[storyIndex].event) playEvent(story[storyIndex]);
  else renderLine();
}
function beginStory() {
  storyIndex = 0;
  jiahaoCharacter.className = 'jiahao-character';
  setScene('gate');
  renderLine();
}
$('#easy-button').addEventListener('click', () => showScreen('character'));
$('#back-button').addEventListener('click', () => showScreen('difficulty'));
document.querySelectorAll('.character-card').forEach((card) => {
  card.addEventListener('click', async () => {
    if (isSceneEventPlaying) return;
    isSceneEventPlaying = true;
    selectedCharacter = card.dataset.character;
    screens.sight.dataset.character = selectedCharacter;
    // 在点击回调内启动音频，兼容需要用户操作才允许播放的浏览器。
    useMusic(peaceAudio);
    transition.classList.remove('play');
    void transition.offsetWidth;
    transition.classList.add('play');
    await pause(500);
    showScreen('sight');
    isSceneEventPlaying = false;
    beginStory();
    dialogue.focus({ preventScroll: true });
  });
});
screens.sight.addEventListener('click', advanceStory);
window.addEventListener('keydown', (event) => {
  if (screens.sight.hidden || event.target === musicToggle ||
      !['Enter', ' ', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  if (!event.repeat) advanceStory();
});
musicToggle.addEventListener('click', () => {
  if (currentMusic.paused && !muted) tryMusic();
  else {
    muted = !muted;
    [menuAudio, peaceAudio, voiceAudio].forEach((audio) => { audio.muted = muted; });
    if (!muted) tryMusic();
  }
  refreshMusicLabel();
});
['pointerdown', 'keydown'].forEach((event) => {
  document.addEventListener(event, () => { if (currentMusic.paused && !muted) tryMusic(); });
});
['ended', 'error'].forEach((event) => {
  voiceAudio.addEventListener(event, () => { currentMusic.volume = .32; });
});
useMusic(menuAudio);
