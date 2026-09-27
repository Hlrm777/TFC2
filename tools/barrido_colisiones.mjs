// Barrido de límites articulares de los brazos con los detectores del visor.
//
// Reproduce los barridos 1D y 2D de agosto (retirados del visor) con EL MISMO
// criterio que el visor aplica en vivo: `probe()` = plano posterior del brazo +
// colisión malla-malla (three-mesh-bvh) brazo-tronco. Paso 5°, rango contiguo
// alrededor de abducción 0, demás DOF en home; el 1D refina a 1°.
//
// Añade un chequeo que el visor NO hace: la unión del hombro y el motor que
// viaja con el brazo contra el tronco, en cada pose admisible. Son las piezas
// que giran distinto en la zona de flexión extendida (P5.3).
//
// No modifica el visor: sirve una copia en memoria con una línea añadida que
// expone sus funciones internas (`window.__T`).
//
// Requisitos: Node 18+ y Playwright (`npm i playwright`, `npx playwright
// install chromium`). En un entorno sin acceso a esm.sh, `--modulos-locales`
// apunta el importmap a ./node_modules (instalar three@0.160.0 y
// three-mesh-bvh@0.7.8 en la carpeta del visor).
//
// Uso, desde la raíz del repositorio:
//   node tools/barrido_colisiones.mjs [--salida barrido.json] [--modulos-locales]
//                                     [--chromium RUTA]
//
// Resultado del 2026-09-27 (P5.3 del plan maestro): reproduce las 37 filas de
// la tabla de agosto sin diferencias en los dos brazos; la zona [-180, -95]
// quedó en limits._TABLE_EXT_L/_R; unión y motor: 0 choques.

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VISOR = path.join(RAIZ, 'telmeow_viewer');
const args = process.argv.slice(2);
const opcion = (n, d) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : d; };
const salida = opcion('--salida', path.join(RAIZ, 'barrido_colisiones.json'));
const locales = args.includes('--modulos-locales');
const rutaChromium = opcion('--chromium', undefined);

// -- copia del visor con sus funciones expuestas ----------------------------
let html = fs.readFileSync(path.join(VISOR, 'telmeow_viewer.html'), 'utf8');
if (locales) {
  html = html
    .replace('"https://esm.sh/three@0.160.0"', '"./node_modules/three/build/three.module.js"')
    .replace('"https://esm.sh/three@0.160.0/examples/jsm/"', '"./node_modules/three/examples/jsm/"')
    .replace('"https://esm.sh/three-mesh-bvh@0.7.8?deps=three@0.160.0"', '"./node_modules/three-mesh-bvh/src/index.js"');
}
const cierre = html.lastIndexOf('</script>');
html = html.slice(0, cierre)
  + '  window.__T = {probe, pose, applyPose, pairHit, meshByFile, backplaneHit, scene, MeshBVH, listo: ()=>collisionReady};\n'
  + html.slice(cierre);

const TIPOS = { '.html': 'text/html', '.js': 'text/javascript', '.stl': 'application/octet-stream', '.png': 'image/png' };
const servidor = http.createServer((req, res) => {
  const url = decodeURIComponent(req.url.split('?')[0]);
  if (url === '/_barrido.html') { res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end(html); }
  const archivo = path.join(VISOR, url);
  if (!archivo.startsWith(VISOR) || !fs.existsSync(archivo) || fs.statSync(archivo).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'Content-Type': TIPOS[path.extname(archivo)] || 'application/octet-stream' });
  fs.createReadStream(archivo).pipe(res);
});
await new Promise(r => servidor.listen(0, '127.0.0.1', r));
const puerto = servidor.address().port;

// -- el barrido, dentro de la página ------------------------------------------
const navegador = await chromium.launch({
  executablePath: rutaChromium,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});
const pagina = await navegador.newPage();
pagina.on('pageerror', e => console.error('error en la página:', e.message));
await pagina.goto(`http://127.0.0.1:${puerto}/_barrido.html`);
await pagina.waitForFunction(() => window.__T && window.__T.listo(), null, { timeout: 180000 });

const resultado = await pagina.evaluate(() => {
  const T = window.__T;
  const home = () => { for (const k in T.pose) T.pose[k] = 0; T.applyPose(); T.scene.updateMatrixWorld(true); };
  const LADOS = {
    L: { a: 'armL', f: 'armLtw', par: 'brazo izq', tag: 'Izq', tronco: ['Tronco_TSI.stl', 'Tronco_TIIA.stl', 'Tronco_TIIP.stl'] },
    R: { a: 'armR', f: 'armRtw', par: 'brazo der', tag: 'Der', tronco: ['Tronco_TSD.stl', 'Tronco_TIDA.stl', 'Tronco_TIDP.stl'] },
  };
  const out = { fecha: new Date().toISOString(), dos_d: {}, uno_d: {}, piezas: {} };

  // 2D: flexión de -180 a 90 cada 5°; abducción desde 0 hacia cada lado cada 5°.
  for (const [s, L] of Object.entries(LADOS)) {
    const filas = [];
    for (let f = -180; f <= 90; f += 5) {
      home(); T.pose[L.f] = f;
      if (T.probe(L.par, L.a, 0)) { filas.push([f, null, null]); continue; }
      let hi = 0; while (hi + 5 <= 90 && !T.probe(L.par, L.a, hi + 5)) hi += 5;
      T.pose[L.a] = 0;
      let lo = 0; while (lo - 5 >= -90 && !T.probe(L.par, L.a, lo - 5)) lo -= 5;
      filas.push([f, lo, hi]);
    }
    out.dos_d[s] = filas;
  }

  // 1D: cada DOF de brazo, demás en home, paso 5° y refinado a 1°.
  const rangos = { armL: [-90, 90], armR: [-90, 90], armLtw: [-180, 90], armRtw: [-180, 90] };
  const par = { armL: 'brazo izq', armLtw: 'brazo izq', armR: 'brazo der', armRtw: 'brazo der' };
  for (const [k, [mn, mx]] of Object.entries(rangos)) {
    const lim = [];
    for (const dir of [-1, 1]) {
      home(); const tope = dir < 0 ? mn : mx; let a = 0;
      while (dir * (a + 5 * dir) <= dir * tope && !T.probe(par[k], k, a + 5 * dir)) a += 5 * dir;
      let fin = a;
      if (fin !== tope) { let b = a; while (dir * (b + dir) <= dir * tope && !T.probe(par[k], k, b + dir)) b += dir; fin = b; }
      lim.push(fin);
    }
    out.uno_d[k] = lim;
  }

  // Unión y motor del brazo contra el tronco, en todas las poses admisibles.
  const extra = ['Tapa_Cuerpo.stl', 'Modulo_Cajita.stl'];
  const bvh = f => { const m = T.meshByFile[f]; if (m && !m.geometry.boundsTree) m.geometry.boundsTree = new T.MeshBVH(m.geometry); };
  for (const [s, L] of Object.entries(LADOS)) {
    const estaticas = [...L.tronco, ...extra];
    estaticas.forEach(bvh);
    const pares = [['union', `M_Hombro${L.tag}_Union.stl`, estaticas], ['motor', `M_Hombro${L.tag}_Base.stl`, estaticas]];
    pares.forEach(([, m]) => bvh(m));
    const choques = { union: [], motor: [] }; let total = 0;
    for (const [f, lo, hi] of out.dos_d[s]) {
      if (lo === null) continue;
      for (let a = lo; a <= hi; a += 5) {
        home(); T.pose[L.f] = f; T.pose[L.a] = a; T.applyPose(); T.scene.updateMatrixWorld(true); total++;
        for (const p of pares) if (T.pairHit(p)) choques[p[0]].push([f, a]);
      }
    }
    home();
    const enHome = {}; for (const p of pares) enHome[p[0]] = T.pairHit(p);
    out.piezas[s] = { poses: total, en_home: enHome, choques };
  }
  home();
  return out;
});

fs.writeFileSync(salida, JSON.stringify(resultado, null, 1));
console.log(`1D: ${JSON.stringify(resultado.uno_d)}`);
for (const s of ['L', 'R']) {
  const p = resultado.piezas[s];
  console.log(`${s}: ${p.poses} poses; choques unión ${p.choques.union.length}, motor ${p.choques.motor.length}`);
}
console.log(`Guardado en ${salida}`);
await navegador.close();
servidor.close();
