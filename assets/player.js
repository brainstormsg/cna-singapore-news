// Ch8 Weekly News playlist player.
// One shared <audio> element: tapping Play on any card starts there, and each
// track moves on to the next card (newest to oldest) when it ends.
// Media Session API gives Android lock-screen / notification controls.
(function () {
  const audio = document.getElementById('player');
  const bar = document.getElementById('bar');
  const barTitle = document.getElementById('barTitle');
  const barPos = document.getElementById('barPos');
  const toggle = document.getElementById('toggle');
  const seek = document.getElementById('seek');
  const cur = document.getElementById('cur');
  const total = document.getElementById('total');
  const cards = Array.from(document.querySelectorAll('.card[data-src]'));
  let index = -1;
  let seeking = false;

  const fmt = s => {
    if (!isFinite(s)) return '0:00';
    s = Math.floor(s);
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  };

  function setIcon() {
    const playing = !audio.paused;
    toggle.textContent = playing ? '⏸' : '▶';
    toggle.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    cards.forEach((c, i) => {
      const btn = c.querySelector('.playbtn');
      const active = i === index;
      c.classList.toggle('playing', active);
      btn.textContent = active && playing ? '⏸ Playing' : (active ? '▶ Resume' : '▶ Play');
    });
    if ('mediaSession' in navigator) navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
  }

  function play(i) {
    if (i < 0 || i >= cards.length) {
      if (i >= cards.length) { audio.pause(); barPos.textContent = 'End of list'; setIcon(); }
      return;
    }
    index = i;
    const c = cards[i];
    audio.src = c.dataset.src;
    audio.play().catch(() => setIcon());
    bar.hidden = false;
    document.body.classList.add('has-bar');
    barTitle.textContent = c.dataset.title;
    barPos.textContent = `${i + 1} of ${cards.length} · ${c.dataset.date}`;
    seek.value = 0;
    cur.textContent = '0:00';
    total.textContent = '0:00';
    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: c.dataset.title,
        artist: '8world news',
        album: 'Ch8 Weekly News',
      });
    }
    setIcon();
  }

  cards.forEach((c, i) => {
    c.querySelector('.playbtn').addEventListener('click', () => {
      if (i === index) { audio.paused ? audio.play() : audio.pause(); }
      else play(i);
    });
  });

  document.getElementById('playall').addEventListener('click', () => play(0));
  toggle.addEventListener('click', () => {
    if (index < 0) return play(0);
    audio.paused ? audio.play() : audio.pause();
  });
  document.getElementById('next').addEventListener('click', () => play(index + 1));
  document.getElementById('prev').addEventListener('click', () => {
    if (audio.currentTime > 3 || index <= 0) audio.currentTime = 0; else play(index - 1);
  });

  // Auto-advance; skip a track that fails to load.
  audio.addEventListener('ended', () => play(index + 1));
  audio.addEventListener('error', () => { if (index >= 0) setTimeout(() => play(index + 1), 800); });
  audio.addEventListener('play', setIcon);
  audio.addEventListener('pause', setIcon);
  audio.addEventListener('loadedmetadata', () => {
    seek.max = Math.floor(audio.duration) || 0;
    total.textContent = fmt(audio.duration);
  });
  audio.addEventListener('timeupdate', () => {
    if (!seeking) seek.value = Math.floor(audio.currentTime);
    cur.textContent = fmt(audio.currentTime);
    if ('mediaSession' in navigator && navigator.mediaSession.setPositionState && isFinite(audio.duration)) {
      try {
        navigator.mediaSession.setPositionState({ duration: audio.duration, position: audio.currentTime, playbackRate: audio.playbackRate });
      } catch (e) {}
    }
  });
  seek.addEventListener('input', () => { seeking = true; cur.textContent = fmt(+seek.value); });
  seek.addEventListener('change', () => { audio.currentTime = +seek.value; seeking = false; });

  if ('mediaSession' in navigator) {
    const ms = navigator.mediaSession;
    const set = (action, fn) => { try { ms.setActionHandler(action, fn); } catch (e) {} };
    set('play', () => audio.play());
    set('pause', () => audio.pause());
    set('nexttrack', () => play(index + 1));
    set('previoustrack', () => play(Math.max(0, index - 1)));
    set('seekto', d => { audio.currentTime = d.seekTime; });
    set('seekbackward', d => { audio.currentTime = Math.max(0, audio.currentTime - (d.seekOffset || 10)); });
    set('seekforward', d => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + (d.seekOffset || 10)); });
  }
})();
