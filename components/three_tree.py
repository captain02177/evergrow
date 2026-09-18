"""
components/three_tree.py

Python wrapper that returns the HTML/JS payload for the hyper-realistic
3D tree, rendered inside Streamlit via `streamlit.components.v1.html`.

Visual approach (photoreal broadleaf/oak tree, realistic outdoor scene):
    - Recursive branch generator (2-3 levels) built from tapered, textured
      cylinder segments -> an organic, spreading oak-like silhouette instead
      of a simple cone.
    - Dense instanced-leaf canopy: hundreds of small "leaf clump" billboards
      (each a canvas-drawn cluster of several leaf blades) scattered through
      an ellipsoid volume around the branch tips via THREE.InstancedMesh,
      biased toward the outer shell for a full, fluffy silhouette.
    - Gradient sky dome + warm directional "sun" light with shadows +
      hemisphere fill light, matched fog for atmospheric depth.
    - Grass-textured ground plane plus hundreds of instanced grass blades.
    - Interactive camera (OrbitControls, 360-degree, touch + mouse).
    - Interactive pest ("bug") spawner + raycaster click/tap handler for the
      Tozalovchi (Cleaner) role; posts a message back to Streamlit when cleared.
    - Golden "Magical Shield" force-field sphere when `shield_active=True`.
    - Fully responsive canvas, auto-resizes to the device/container width.

Growth stages (see database.stage_for_level) still drive color/detail:
    seed -> sprout -> golden_pink -> ecosystem -> fairytale
but all stages now share the same realistic branch+leaf-instancing technique,
just with different bark/leaf palettes and stage extras (grass density,
wildlife, cottage).
"""

from __future__ import annotations

import json

import streamlit.components.v1 as components

THREE_JS_CDN = "https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js"
ORBIT_CONTROLS_CDN = "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"


def render_tree(
    level: int,
    stage: str,
    shield_active: bool = False,
    pest_mode: bool = False,
    pest_count: int = 6,
    height: int = 560,
    key: str = "three_tree",
) -> None:
    """
    Render the 3D tree canvas.

    Args:
        level: current tree level (drives scale/branch-count/detail).
        stage: one of 'seed' | 'sprout' | 'golden_pink' | 'ecosystem' | 'fairytale'
               (see database.stage_for_level).
        shield_active: draw the translucent golden force-field sphere.
        pest_mode: if True, spawn clickable pests on the trunk/branches for the
                   Tozalovchi role; posts {type:'pests_cleared'} to the parent
                   window (Streamlit) once all pests are removed.
        pest_count: number of pests to spawn when pest_mode is True.
        height: canvas height in px.
        key: unique Streamlit component key.
    """
    html = _build_html(
        level=level,
        stage=stage,
        shield_active=shield_active,
        pest_mode=pest_mode,
        pest_count=pest_count,
        key=key,
    )
    components.html(html, height=height, scrolling=False)


def _build_html(
    level: int, stage: str, shield_active: bool, pest_mode: bool, pest_count: int, key: str
) -> str:
    config = {
        "level": level,
        "stage": stage,
        "shieldActive": shield_active,
        "pestMode": pest_mode,
        "pestCount": pest_count,
        "key": key,
    }
    config_json = json.dumps(config)

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
<style>
  html, body {{ margin:0; padding:0; overflow:hidden; background:transparent; }}
  #tree-canvas-wrap {{ width:100%; height:100vh; position:relative; }}
  #tree-canvas-wrap canvas {{ display:block; width:100% !important; height:100% !important; touch-action:none; }}
  #hud {{
    position:absolute; top:8px; left:8px; color:#eafff0; font-family:'Segoe UI',sans-serif;
    font-size:12px; background:rgba(10,30,15,0.45); padding:6px 10px; border-radius:8px;
    pointer-events:none; letter-spacing:.3px;
  }}
  #pest-counter {{
    position:absolute; top:8px; right:8px; color:#fff; font-family:'Segoe UI',sans-serif;
    font-size:13px; background:rgba(120,20,20,0.55); padding:6px 12px; border-radius:8px;
    display:none;
  }}
</style>
</head>
<body>
<div id="tree-canvas-wrap">
  <div id="hud">🌳 Level {level} — {stage.replace('_',' ').title()}</div>
  <div id="pest-counter">🐛 Pests left: <span id="pest-left">{pest_count}</span></div>
</div>

<script src="{THREE_JS_CDN}"></script>
<script src="{ORBIT_CONTROLS_CDN}"></script>
<script>
const CONFIG = {config_json};

// --------------------------------------------------------------------- //
// Palettes per growth stage
// --------------------------------------------------------------------- //
const PALETTES = {{
  seed:        {{ bark: '#4a3420', barkDark: '#2b1d10', leaf: ['#3f7d3f','#4f9450','#356b35'], sky: ['#bcd9ea','#eaf6ef'], ground: '#3a2c18', grass: ['#5c8a4a','#4a7038'] }},
  sprout:      {{ bark: '#5b4026', barkDark: '#33230f', leaf: ['#4caf50','#6dc06d','#3f8f42'], sky: ['#8fc7ec','#eaf6ef'], ground: '#3f6b34', grass: ['#6fb356','#5a9942'] }},
  golden_pink: {{ bark: '#b8862b', barkDark: '#7a5310', leaf: ['#ffb6d9','#ff9ecb','#ffd3e8'], sky: ['#a7d4f0','#fdeef7'], ground: '#4a7a3c', grass: ['#79c05e','#65a94c'] }},
  ecosystem:   {{ bark: '#9c6a2c', barkDark: '#5f3f16', leaf: ['#ff8fc6','#ffb0d8','#ff6fb0'], sky: ['#8fd0f5','#eaf8ff'], ground: '#4f8240', grass: ['#7fc763','#67ac4e'] }},
  fairytale:   {{ bark: '#a9762f', barkDark: '#6b4718', leaf: ['#ffb3da','#ffc9e5','#ff9bcf'], sky: ['#ffd9a0','#fff2df'], ground: '#54864a', grass: ['#84c968','#6cae53'] }},
}};
const P = PALETTES[CONFIG.stage] || PALETTES.sprout;

// --------------------------------------------------------------------- //
// Scene / Camera / Renderer
// --------------------------------------------------------------------- //
const wrap = document.getElementById('tree-canvas-wrap');
const scene = new THREE.Scene();

const horizonColor = new THREE.Color(P.sky[1]);
scene.fog = new THREE.Fog(horizonColor.getHex(), 22, 55);

const camera = new THREE.PerspectiveCamera(48, wrap.clientWidth / wrap.clientHeight, 0.1, 500);
camera.position.set(0, 6, 17);

const renderer = new THREE.WebGLRenderer({{ antialias: true, alpha: true }});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(wrap.clientWidth, wrap.clientHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputEncoding = THREE.sRGBEncoding;
wrap.appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 6;
controls.maxDistance = 32;
controls.maxPolarAngle = Math.PI * 0.495;
controls.target.set(0, 3.5, 0);

// --------------------------------------------------------------------- //
// Sky dome (gradient) + sun glow
// --------------------------------------------------------------------- //
function buildSkyDome() {{
  const skyGeo = new THREE.SphereGeometry(140, 24, 16);
  const zenith = new THREE.Color(P.sky[0]);
  const horizon = new THREE.Color(P.sky[1]);
  const colors = [];
  const posAttr = skyGeo.attributes.position;
  for (let i = 0; i < posAttr.count; i++) {{
    const y = posAttr.getY(i) / 140; // -1..1
    const tt = THREE.MathUtils.clamp(y * 0.9 + 0.35, 0, 1);
    const c = horizon.clone().lerp(zenith, tt);
    colors.push(c.r, c.g, c.b);
  }}
  skyGeo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  const skyMat = new THREE.MeshBasicMaterial({{ vertexColors: true, side: THREE.BackSide, fog: false }});
  const sky = new THREE.Mesh(skyGeo, skyMat);
  scene.add(sky);
}}
buildSkyDome();

// Soft sun glow sprite
function buildSunGlow() {{
  const c = document.createElement('canvas');
  c.width = 128; c.height = 128;
  const ctx = c.getContext('2d');
  const grad = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, 'rgba(255,250,220,0.95)');
  grad.addColorStop(0.4, 'rgba(255,240,180,0.45)');
  grad.addColorStop(1, 'rgba(255,240,180,0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  const mat = new THREE.SpriteMaterial({{ map: tex, transparent: true, depthWrite: false, fog: false }});
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(30, 30, 1);
  sprite.position.set(-40, 34, -60);
  scene.add(sprite);
}}
buildSunGlow();

// --------------------------------------------------------------------- //
// Lighting — warm directional sun + hemisphere fill
// --------------------------------------------------------------------- //
const hemi = new THREE.HemisphereLight(new THREE.Color(P.sky[0]), new THREE.Color(P.ground), 0.65);
scene.add(hemi);

const sun = new THREE.DirectionalLight(0xfff1d0, 1.25);
sun.position.set(-16, 22, -12);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.left = -18; sun.shadow.camera.right = 18;
sun.shadow.camera.top = 18; sun.shadow.camera.bottom = -18;
sun.shadow.camera.near = 1; sun.shadow.camera.far = 60;
sun.shadow.bias = -0.0015;
scene.add(sun);

const fill = new THREE.DirectionalLight(0xcfe8ff, 0.25);
fill.position.set(14, 10, 14);
scene.add(fill);

// --------------------------------------------------------------------- //
// Ground: grass-textured plane + instanced grass blades
// --------------------------------------------------------------------- //
function buildGrassTexture() {{
  const c = document.createElement('canvas');
  c.width = 256; c.height = 256;
  const ctx = c.getContext('2d');
  ctx.fillStyle = P.ground;
  ctx.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 1400; i++) {{
    const shade = Math.random() > 0.5 ? P.grass[0] : P.grass[1];
    ctx.fillStyle = shade;
    ctx.globalAlpha = 0.35 + Math.random() * 0.4;
    const x = Math.random() * 256, y = Math.random() * 256;
    const w = 1 + Math.random() * 2, h = 3 + Math.random() * 5;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(Math.random() * Math.PI);
    ctx.fillRect(-w / 2, -h / 2, w, h);
    ctx.restore();
  }}
  ctx.globalAlpha = 1;
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(10, 10);
  return tex;
}}

const groundGeo = new THREE.CircleGeometry(45, 64);
const groundMat = new THREE.MeshStandardMaterial({{ map: buildGrassTexture(), roughness: 1 }});
const ground = new THREE.Mesh(groundGeo, groundMat);
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
scene.add(ground);

function buildGrassBlades(count) {{
  const bladeGeo = new THREE.ConeGeometry(0.045, 0.5, 3);
  bladeGeo.translate(0, 0.25, 0);
  const bladeMat = new THREE.MeshStandardMaterial({{ color: P.grass[0], roughness: 1 }});
  const inst = new THREE.InstancedMesh(bladeGeo, bladeMat, count);
  const dummy = new THREE.Object3D();
  for (let i = 0; i < count; i++) {{
    const a = Math.random() * Math.PI * 2;
    const r = 2.2 + Math.pow(Math.random(), 0.5) * 16;
    dummy.position.set(Math.cos(a) * r, 0, Math.sin(a) * r);
    dummy.rotation.y = Math.random() * Math.PI;
    dummy.rotation.z = (Math.random() - 0.5) * 0.2;
    const s = 0.7 + Math.random() * 0.8;
    dummy.scale.set(s, s * (0.8 + Math.random() * 0.6), s);
    dummy.updateMatrix();
    inst.setMatrixAt(i, dummy.matrix);
  }}
  inst.instanceMatrix.needsUpdate = true;
  inst.castShadow = false;
  scene.add(inst);
}}
buildGrassBlades(CONFIG.stage === 'seed' ? 120 : 420);

// --------------------------------------------------------------------- //
// Bark texture
// --------------------------------------------------------------------- //
function buildBarkTexture() {{
  const c = document.createElement('canvas');
  c.width = 128; c.height = 256;
  const ctx = c.getContext('2d');
  ctx.fillStyle = P.bark;
  ctx.fillRect(0, 0, 128, 256);
  for (let i = 0; i < 90; i++) {{
    ctx.strokeStyle = Math.random() > 0.5 ? P.barkDark : P.bark;
    ctx.globalAlpha = 0.25 + Math.random() * 0.35;
    ctx.lineWidth = 1 + Math.random() * 2;
    ctx.beginPath();
    const x = Math.random() * 128;
    ctx.moveTo(x, 0);
    let cx = x;
    for (let y = 0; y <= 256; y += 32) {{
      cx += (Math.random() - 0.5) * 10;
      ctx.lineTo(cx, y);
    }}
    ctx.stroke();
  }}
  ctx.globalAlpha = 1;
  for (let i = 0; i < 4; i++) {{
    ctx.fillStyle = P.barkDark;
    ctx.globalAlpha = 0.3;
    ctx.beginPath();
    ctx.ellipse(Math.random() * 128, Math.random() * 256, 6, 4, Math.random(), 0, Math.PI * 2);
    ctx.fill();
  }}
  ctx.globalAlpha = 1;
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(2, 3);
  return tex;
}}
const barkTex = buildBarkTexture();
const barkMat = new THREE.MeshStandardMaterial({{ map: barkTex, roughness: 0.95, metalness: 0.02 }});

// --------------------------------------------------------------------- //
// Leaf-clump texture (several overlapping leaf blades per sprite/instance)
// --------------------------------------------------------------------- //
function buildLeafClumpTexture() {{
  const c = document.createElement('canvas');
  c.width = 128; c.height = 128;
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, 128, 128);
  const colors = P.leaf;
  for (let i = 0; i < 9; i++) {{
    const cx = 30 + Math.random() * 68;
    const cy = 30 + Math.random() * 68;
    const len = 22 + Math.random() * 20;
    const wid = 10 + Math.random() * 8;
    const ang = Math.random() * Math.PI * 2;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(ang);
    ctx.fillStyle = colors[Math.floor(Math.random() * colors.length)];
    ctx.globalAlpha = 0.85 + Math.random() * 0.15;
    ctx.beginPath();
    ctx.ellipse(0, 0, len / 2, wid / 2, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,0.15)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(-len / 2, 0);
    ctx.lineTo(len / 2, 0);
    ctx.stroke();
    ctx.restore();
  }}
  ctx.globalAlpha = 1;
  const tex = new THREE.CanvasTexture(c);
  return tex;
}}
const leafTex = buildLeafClumpTexture();

// --------------------------------------------------------------------- //
// Recursive branch builder -> organic, spreading oak-like silhouette
// --------------------------------------------------------------------- //
const treeGroup = new THREE.Group();
scene.add(treeGroup);

const branchTips = [];

function addBranchSegment(origin, direction, length, radiusStart, radiusEnd) {{
  const geo = new THREE.CylinderGeometry(radiusEnd, radiusStart, length, 8, 1);
  geo.translate(0, length / 2, 0);
  const mesh = new THREE.Mesh(geo, barkMat);
  mesh.position.copy(origin);
  const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.clone().normalize());
  mesh.quaternion.copy(quat);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  treeGroup.add(mesh);
  return origin.clone().add(direction.clone().normalize().multiplyScalar(length));
}}

function growBranch(origin, direction, length, radius, depth, maxDepth) {{
  if (length < 0.15 || radius < 0.01) {{
    branchTips.push(origin.clone());
    return;
  }}
  const radiusEnd = radius * 0.62;
  const tip = addBranchSegment(origin, direction, length, radius, radiusEnd);

  if (depth >= maxDepth) {{
    branchTips.push(tip);
    return;
  }}

  const children = depth === 0 ? 3 : (2 + Math.floor(Math.random() * 2));
  for (let i = 0; i < children; i++) {{
    const spread = 0.55 + Math.random() * 0.5;
    const axis = new THREE.Vector3(Math.random() - 0.5, Math.random() * 0.3, Math.random() - 0.5).normalize();
    const childDir = direction.clone().applyAxisAngle(axis, spread).normalize();
    childDir.y = Math.max(childDir.y, 0.15);
    childDir.normalize();
    const childLength = length * (0.62 + Math.random() * 0.16);
    const childRadius = radiusEnd * (0.75 + Math.random() * 0.2);
    growBranch(tip, childDir, childLength, childRadius, depth + 1, maxDepth);
  }}
}}

function buildTree(level) {{
  const scale = CONFIG.stage === 'seed' ? 0.18 : Math.min(1.7, 0.75 + level * 0.025);
  const trunkHeight = 3.4 * scale;
  const trunkRadius = 0.42 * scale;
  const maxDepth = CONFIG.stage === 'seed' ? 1 : 3;
  growBranch(new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 1, 0), trunkHeight, trunkRadius, 0, maxDepth);
  return {{ scale, trunkHeight }};
}}
const treeInfo = buildTree(CONFIG.level);

// --------------------------------------------------------------------- //
// Instanced leaf canopy — scattered through an ellipsoid around the tips
// --------------------------------------------------------------------- //
function buildCanopy(tips, scale) {{
  if (CONFIG.stage === 'seed' || tips.length === 0) return null;

  let cx = 0, cy = 0, cz = 0, maxY = 0;
  tips.forEach(p => {{ cx += p.x; cy += p.y; cz += p.z; maxY = Math.max(maxY, p.y); }});
  cx /= tips.length; cy /= tips.length; cz /= tips.length;
  const center = new THREE.Vector3(cx, cy * 0.55 + maxY * 0.45, cz);
  const radius = Math.max(2.2 * scale, maxY * 0.55);

  const leafPlaneGeo = new THREE.PlaneGeometry(1.6, 1.6);
  const leafMat = new THREE.MeshStandardMaterial({{
    map: leafTex, transparent: true, alphaTest: 0.35, side: THREE.DoubleSide, roughness: 0.85,
  }});
  const count = Math.min(520, 160 + Math.floor(scale * 220));
  const inst = new THREE.InstancedMesh(leafPlaneGeo, leafMat, count);
  const dummy = new THREE.Object3D();

  for (let i = 0; i < count; i++) {{
    const u = Math.random(), v = Math.random(), w = Math.random();
    const rr = radius * (0.55 + 0.45 * Math.cbrt(Math.max(u, v, w)));
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const px = center.x + rr * Math.sin(phi) * Math.cos(theta);
    const py = center.y + rr * Math.cos(phi) * 0.85;
    const pz = center.z + rr * Math.sin(phi) * Math.sin(theta);
    dummy.position.set(px, Math.max(py, treeInfo.trunkHeight * 0.5), pz);
    dummy.rotation.set(Math.random() * Math.PI, Math.random() * Math.PI, Math.random() * Math.PI);
    const s = 0.8 + Math.random() * 0.9;
    dummy.scale.set(s, s, s);
    dummy.updateMatrix();
    inst.setMatrixAt(i, dummy.matrix);
  }}
  inst.instanceMatrix.needsUpdate = true;
  inst.castShadow = true;
  inst.receiveShadow = true;
  treeGroup.add(inst);
  return {{ center, radius, mesh: inst }};
}}
const canopy = buildCanopy(branchTips, treeInfo.scale);

// --------------------------------------------------------------------- //
// Stage extras: wildlife, cottage (levels 16+/31+)
// --------------------------------------------------------------------- //
if (['ecosystem','fairytale'].includes(CONFIG.stage)) {{
  const critterMat = new THREE.MeshStandardMaterial({{ color: 0xffffff, roughness: 0.8 }});
  for (let i = 0; i < 3; i++) {{
    const sheep = new THREE.Mesh(new THREE.SphereGeometry(0.5, 10, 10), critterMat);
    const a = Math.random() * Math.PI * 2, r = 5 + Math.random() * 7;
    sheep.position.set(Math.cos(a) * r, 0.5, Math.sin(a) * r);
    sheep.castShadow = true;
    sheep.userData.orbit = {{ a, r, speed: 0.05 + Math.random() * 0.05 }};
    sheep.userData.isCritter = true;
    scene.add(sheep);
  }}
}}

if (CONFIG.stage === 'fairytale') {{
  const cottageGroup = new THREE.Group();
  const wallMat = new THREE.MeshStandardMaterial({{ color: 0xe8c79b }});
  const roofMat = new THREE.MeshStandardMaterial({{ color: 0xaa3b3b }});
  const base = new THREE.Mesh(new THREE.BoxGeometry(2.4, 1.6, 2.4), wallMat);
  base.position.set(9, 0.8, -3);
  const roof = new THREE.Mesh(new THREE.ConeGeometry(1.9, 1.3, 4), roofMat);
  roof.position.set(9, 2.25, -3);
  roof.rotation.y = Math.PI / 4;
  cottageGroup.add(base, roof);
  cottageGroup.traverse(o => {{ o.castShadow = true; o.receiveShadow = true; }});
  scene.add(cottageGroup);
}}

// --------------------------------------------------------------------- //
// Magical Shield force-field
// --------------------------------------------------------------------- //
let shieldMesh = null;
if (CONFIG.shieldActive) {{
  const shieldRadius = canopy ? canopy.radius * 1.35 : treeInfo.trunkHeight * 1.2;
  const shieldGeo = new THREE.SphereGeometry(shieldRadius, 32, 32);
  const shieldMat = new THREE.MeshPhysicalMaterial({{
    color: 0xffd966, transparent: true, opacity: 0.2, roughness: 0.1,
    metalness: 0.1, emissive: 0xffcf40, emissiveIntensity: 0.35,
  }});
  shieldMesh = new THREE.Mesh(shieldGeo, shieldMat);
  shieldMesh.position.y = (canopy ? canopy.center.y : treeInfo.trunkHeight) * 0.9;
  scene.add(shieldMesh);
}}

// --------------------------------------------------------------------- //
// Particles: falling leaves / glowing spores
// --------------------------------------------------------------------- //
const particleCount = 50;
const particleGeo = new THREE.BufferGeometry();
const positions = new Float32Array(particleCount * 3);
for (let i = 0; i < particleCount; i++) {{
  positions[i*3] = (Math.random() - 0.5) * 18;
  positions[i*3+1] = Math.random() * 11;
  positions[i*3+2] = (Math.random() - 0.5) * 18;
}}
particleGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
const particleMat = new THREE.PointsMaterial({{
  color: P.leaf[0], size: 0.1, transparent: true, opacity: 0.75,
}});
const particles = new THREE.Points(particleGeo, particleMat);
scene.add(particles);

// --------------------------------------------------------------------- //
// Pest spawner + raycaster (Tozalovchi role)
// --------------------------------------------------------------------- //
const pests = [];
let pestsRemaining = 0;

if (CONFIG.pestMode) {{
  pestsRemaining = CONFIG.pestCount;
  document.getElementById('pest-counter').style.display = 'block';
  const pestMat = new THREE.MeshStandardMaterial({{ color: 0x3a2a1a, roughness: 0.6 }});
  for (let i = 0; i < CONFIG.pestCount; i++) {{
    const bug = new THREE.Group();
    const body = new THREE.Mesh(new THREE.SphereGeometry(0.16, 8, 8), pestMat);
    bug.add(body);
    for (let l = 0; l < 6; l++) {{
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.018, 0.22, 4), pestMat);
      const ang = (l / 6) * Math.PI * 2;
      leg.position.set(Math.cos(ang) * 0.16, -0.09, Math.sin(ang) * 0.16);
      leg.rotation.z = Math.PI / 2.2;
      bug.add(leg);
    }}
    const h = Math.random() * treeInfo.trunkHeight * 1.4;
    const ang = Math.random() * Math.PI * 2;
    const rad = 0.4 * treeInfo.scale + Math.random() * 0.35;
    bug.position.set(Math.cos(ang) * rad, h, Math.sin(ang) * rad);
    bug.userData.isPest = true;
    treeGroup.add(bug);
    pests.push(bug);
  }}

  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();

  function handlePointer(clientX, clientY) {{
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const targets = pests.flatMap(p => p.children);
    const hits = raycaster.intersectObjects(targets, false);
    if (hits.length > 0) {{
      let hitObj = hits[0].object;
      const bug = pests.find(p => p.children.includes(hitObj));
      if (bug) {{
        treeGroup.remove(bug);
        const idx = pests.indexOf(bug);
        if (idx > -1) pests.splice(idx, 1);
        pestsRemaining -= 1;
        document.getElementById('pest-left').innerText = pestsRemaining;
        if (pestsRemaining <= 0) {{
          document.getElementById('pest-counter').innerText = '✅ All pests cleared!';
          try {{
            window.parent.postMessage({{ type: 'pests_cleared', key: '{key}' }}, '*');
          }} catch (e) {{}}
        }}
      }}
    }}
  }}

  renderer.domElement.addEventListener('click', (e) => handlePointer(e.clientX, e.clientY));
  renderer.domElement.addEventListener('touchstart', (e) => {{
    if (e.touches.length > 0) {{
      handlePointer(e.touches[0].clientX, e.touches[0].clientY);
    }}
  }}, {{ passive: true }});
}}

// --------------------------------------------------------------------- //
// Resize handling
// --------------------------------------------------------------------- //
function onResize() {{
  const w = wrap.clientWidth, h = wrap.clientHeight;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
}}
window.addEventListener('resize', onResize);

// --------------------------------------------------------------------- //
// Animation loop
// --------------------------------------------------------------------- //
let t = 0;
function animate() {{
  requestAnimationFrame(animate);
  t += 0.01;

  treeGroup.rotation.y += 0.0006;

  if (shieldMesh) {{
    shieldMesh.rotation.y += 0.003;
    shieldMesh.material.opacity = 0.16 + Math.sin(t * 2) * 0.06;
  }}

  const posAttr = particleGeo.attributes.position;
  for (let i = 0; i < particleCount; i++) {{
    posAttr.array[i*3+1] -= 0.01;
    if (posAttr.array[i*3+1] < 0) posAttr.array[i*3+1] = 11;
  }}
  posAttr.needsUpdate = true;

  scene.children.forEach(obj => {{
    if (obj.userData && obj.userData.isCritter) {{
      obj.userData.orbit.a += obj.userData.orbit.speed * 0.01;
      obj.position.x = Math.cos(obj.userData.orbit.a) * obj.userData.orbit.r;
      obj.position.z = Math.sin(obj.userData.orbit.a) * obj.userData.orbit.r;
    }}
  }});

  controls.update();
  renderer.render(scene, camera);
}}
animate();
</script>
</body>
</html>
"""
