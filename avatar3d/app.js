import * as THREE from 'three';
import { GLTFLoader } from './vendor/GLTFLoader.js';

const COLORS = { LISTENING: '#00ff88', THINKING: '#ffcc00', PROCESSING: '#ffcc00', SPEAKING: '#ff6b00', SLEEPING: '#3a8a9a', MUTED: '#ff3366' };
const canvas = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
const scene = new THREE.Scene();
scene.background = new THREE.Color('#00060a');
const camera = new THREE.PerspectiveCamera(28, 1, 0.05, 50);

const hemi = new THREE.HemisphereLight('#bfe8ff', '#0a1a2a', 1.2);
const key = new THREE.DirectionalLight('#ffffff', 2.2); key.position.set(1.2, 2.4, 2.0);
const fill = new THREE.DirectionalLight('#9fd8ff', 0.8); fill.position.set(-1.5, 1.0, 1.5);
const rim = new THREE.DirectionalLight('#00d4ff', 1.6); rim.position.set(-2, 1.5, -2);
scene.add(hemi, key, fill, rim);

const state = { name: 'LISTENING', level: 0, open: 0, width: 0, framing: 'bust' };
const smooth = { open: 0, width: 0, nod: 0, yaw: 0, pitch: 0, glanceX: 0, glanceY: 0, glanceUntil: 0, light: 1 };
let model = null, mixer = null, head = null;
const eyes = [];
const restQ = new Map();          // orientamento nello spazio della posa neutra, per testa e occhi
const headPos = new THREE.Vector3(0, 1.6, 0);
const morphMeshes = [];
const mouth = { hasShapes: false, map: {} };
const blink = { next: 2, until: 0 };
const levels = new Array(36).fill(0);
const clock = new THREE.Clock();
let modelHeight = 1.7;
const modelCenter = new THREE.Vector3();

function resize() {
  const w = window.innerWidth || canvas.clientWidth, h = window.innerHeight || canvas.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
resize();

function findMorphs(root) {
  root.traverse((o) => {
    if (o.isMesh && o.morphTargetDictionary && o.morphTargetInfluences) morphMeshes.push(o);
  });
  const names = new Set();
  for (const m of morphMeshes) for (const k of Object.keys(m.morphTargetDictionary)) names.add(k);
  const pick = (...cands) => cands.find((c) => names.has(c));
  mouth.map = {
    open: pick('jawOpen', 'JawOpen', 'mouthOpen', 'MouthOpen', 'viseme_aa', 'mouth_open', 'A'),
    aa: pick('viseme_aa'),
    smile: pick('mouthSmileLeft', 'mouthSmile', 'mouthSmile_L'),
    smileR: pick('mouthSmileRight', 'mouthSmile_R'),
    wide: pick('viseme_I', 'viseme_E'),
    round: pick('viseme_O', 'mouthFunnel', 'mouthPucker', 'O'),
    pucker: pick('mouthPucker'),
    blinkL: pick('eyeBlinkLeft', 'eyesClosed'),
    blinkR: pick('eyeBlinkRight'),
    browUp: pick('browInnerUp'),
    close: pick('mouthClose'),
  };
  mouth.hasShapes = Boolean(mouth.map.open);
  document.getElementById('hint').textContent = (mouth.hasShapes ? 'labiale: attiva' : 'labiale: modello senza blend shape') + ' · clic: busto / figura intera';
}

function setMorph(name, value) {
  if (!name) return;
  for (const m of morphMeshes) {
    const i = m.morphTargetDictionary[name];
    if (i !== undefined) m.morphTargetInfluences[i] = value;
  }
}

function measure() {
  const box = new THREE.Box3().setFromObject(model);
  modelHeight = Math.max(0.1, box.max.y - box.min.y);
  box.getCenter(modelCenter);
}

function frame() {
  if (!model) return;
  if (head) head.getWorldPosition(headPos);
  const h = modelHeight;
  if (state.framing === 'bust') {
    camera.position.set(headPos.x, headPos.y + h * 0.02, headPos.z + h * 0.56);
    camera.lookAt(headPos.x, headPos.y - h * 0.015, headPos.z);
  } else {
    camera.position.set(modelCenter.x, modelCenter.y + h * 0.05, modelCenter.z + h * 1.9);
    camera.lookAt(modelCenter.x, modelCenter.y, modelCenter.z);
  }
}
canvas.addEventListener('click', () => { state.framing = state.framing === 'bust' ? 'full' : 'bust'; frame(); });

const MODEL = new URLSearchParams(location.search).get('model') || 'allegra_2';
new GLTFLoader().load(`./models/${encodeURIComponent(MODEL)}.glb`, (gltf) => {
  model = gltf.scene;
  scene.add(model);
  model.traverse((o) => {
    if (o.isBone) {
      if (o.name === 'Head') head = o;
      else if (o.name === 'LeftEye' || o.name === 'RightEye') eyes.push(o);
    }
    if (o.isMesh) o.frustumCulled = false;
  });
  findMorphs(model);
  model.updateMatrixWorld(true);
  for (const b of [head, ...eyes]) if (b) restQ.set(b, b.getWorldQuaternion(new THREE.Quaternion()));
  let clipTracks = 0;
  if (gltf.animations.length) {
    mixer = new THREE.AnimationMixer(model);
    const clip = gltf.animations[0].clone();
    // Testa e occhi li orientiamo noi; il bacino resta fermo così il corpo guarda avanti.
    clip.tracks = clip.tracks.filter((tr) => !/^(Head|Neck|Hips|LeftEye|RightEye)\./.test(tr.name));
    clipTracks = clip.tracks.length;
    const action = mixer.clipAction(clip);
    action.setLoop(THREE.LoopRepeat); action.play();
  }
  document.getElementById('msg').remove();
  measure();
  if (head) head.getWorldPosition(headPos);
  console.log('modello', MODEL, ': altezza', modelHeight.toFixed(3), 'testa', headPos.toArray().map((v) => v.toFixed(2)).join(','), 'blend shape', mouth.hasShapes, 'occhi', eyes.length, 'tracce', clipTracks);
  frame();
}, undefined, (err) => {
  document.getElementById('msg').textContent = 'Modello non caricato: ' + (err.message || err);
});

const qOffset = new THREE.Quaternion(), qParent = new THREE.Quaternion(), euler = new THREE.Euler();
/** Orienta un osso nello spazio: posa neutra + rotazione (pitch, yaw) in assi mondo. */
function aimBone(bone, pitch, yaw, roll) {
  const rest = restQ.get(bone);
  if (!rest || !bone.parent) return;
  bone.parent.updateWorldMatrix(true, false);
  bone.parent.getWorldQuaternion(qParent).invert();
  euler.set(pitch, yaw, roll || 0, 'YXZ');
  qOffset.setFromEuler(euler);
  bone.quaternion.copy(qParent).multiply(qOffset).multiply(rest);
}

function render() {
  requestAnimationFrame(render);
  const dt = Math.min(0.05, clock.getDelta());
  const t = clock.elapsedTime;
  if (mixer) mixer.update(dt);

  // Posa per stato (pitch positivo = guarda in basso)
  let targetPitch = 0, targetYaw = 0, light = 1;
  if (state.name === 'THINKING' || state.name === 'PROCESSING') { targetPitch = -0.14; targetYaw = 0.28 + Math.sin(t * 0.7) * 0.05; }
  else if (state.name === 'SLEEPING') { targetPitch = 0.32; light = 0.45; }
  else if (state.name === 'LISTENING') { targetYaw = Math.sin(t * 0.5) * 0.05; targetPitch = Math.sin(t * 0.8) * 0.02; }
  else if (state.name === 'SPEAKING') { targetYaw = Math.sin(t * 0.9) * 0.04; }
  if (t < smooth.glanceUntil) { targetYaw += smooth.glanceX; targetPitch += smooth.glanceY; }
  const k = Math.min(1, dt * 4);
  smooth.yaw += (targetYaw - smooth.yaw) * k;
  smooth.pitch += (targetPitch - smooth.pitch) * k;
  smooth.light += (light - smooth.light) * k;
  hemi.intensity = 1.2 * smooth.light; key.intensity = 2.2 * smooth.light; fill.intensity = 0.8 * smooth.light;

  const nodTarget = state.name === 'SPEAKING' ? state.level * 0.07 : 0;
  smooth.nod += (nodTarget - smooth.nod) * Math.min(1, dt * 12);

  if (head) aimBone(head, smooth.pitch + smooth.nod, smooth.yaw, Math.sin(t * 0.9) * 0.012);
  for (const e of eyes) aimBone(e, smooth.pitch * 0.5 + Math.sin(t * 0.37) * 0.03, smooth.yaw * 0.6 + Math.sin(t * 0.23) * 0.04, 0);

  // Bocca e viso
  const openTarget = state.name === 'SPEAKING' ? state.open : 0;
  smooth.open += (openTarget - smooth.open) * Math.min(1, dt * 18);
  smooth.width += ((state.name === 'SPEAKING' ? state.width : 0) - smooth.width) * Math.min(1, dt * 12);
  if (mouth.hasShapes) {
    const o = Math.min(1, smooth.open), w = smooth.width;
    setMorph(mouth.map.open, o * 0.45);
    setMorph(mouth.map.aa, o * 0.55 * (1 - Math.abs(w) * 0.5));
    setMorph(mouth.map.wide, Math.max(0, w) * o * 0.6);
    setMorph(mouth.map.round, Math.max(0, -w) * o * 0.7);
    setMorph(mouth.map.pucker, Math.max(0, -w) * o * 0.2);
    const smileBase = state.name === 'LISTENING' ? 0.07 : 0.03;
    setMorph(mouth.map.smile, smileBase + Math.max(0, w) * 0.2);
    setMorph(mouth.map.smileR, smileBase + Math.max(0, w) * 0.2);
    setMorph(mouth.map.close, 0);   // usato da solo deforma le labbra: mai a riposo
    setMorph(mouth.map.browUp, state.name === 'THINKING' || state.name === 'PROCESSING' ? 0.5 : (state.name === 'LISTENING' ? 0.15 : 0));
    if (t > blink.next) { blink.until = t + 0.13; blink.next = t + 2 + Math.random() * 4; }
    const closed = state.name === 'SLEEPING' ? 1 : (t < blink.until ? 1 : 0);
    setMorph(mouth.map.blinkL, closed); setMorph(mouth.map.blinkR, closed);
  }
  if (state.framing === 'bust' && head) frame();
  renderer.render(scene, camera);
  drawOverlay();
}

const wave = document.getElementById('wave'), wctx = wave.getContext('2d'), stateEl = document.getElementById('state');
let dispLevel = 0;
function drawOverlay() {
  dispLevel += (state.level - dispLevel) * 0.3;
  levels.push(dispLevel); levels.shift();
  const col = COLORS[state.name] || '#00d4ff';
  stateEl.style.color = col; stateEl.style.textShadow = `0 0 8px ${col}88`;
  stateEl.textContent = '●  ' + state.name;
  wctx.clearRect(0, 0, wave.width, wave.height);
  const n = levels.length, bw = wave.width / n;
  for (let i = 0; i < n; i++) {
    const h = 3 + levels[i] * (wave.height - 4) + Math.sin(i * 0.7 + performance.now() / 600) * 1.5;
    wctx.fillStyle = col; wctx.globalAlpha = 0.35 + 0.65 * (i / n);
    wctx.fillRect(i * bw + 1, (wave.height - h) / 2, bw - 3, h);
  }
  wctx.globalAlpha = 1;
}

window.avatar3d = {
  update(level, open, width, name) { state.level = level; state.open = open; state.width = width; if (name) state.name = name; },
  setState(name) { state.name = name; },
  glance(dx, dy, hold) { smooth.glanceX = dx * 0.3; smooth.glanceY = -dy * 0.2; smooth.glanceUntil = clock.elapsedTime + (hold || 1.1); },
  setFraming(f) { state.framing = f; frame(); },
  status() { return { loaded: Boolean(model), animation: Boolean(mixer), head: Boolean(head), blendShapes: mouth.hasShapes, morphs: morphMeshes.length, eyes: eyes.length }; },
};
render();
