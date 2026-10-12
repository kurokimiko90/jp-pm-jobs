// 本番模擬：台本全体（100 問前後）から実際の面接に近い分量を抜き出す。
// 1 問 = 面接官の発話（複数行可）＋直後の候補者の回答。開場・収尾の幕は丸ごと残し、
// 中盤の各幕から最低 1 問＋残りを無作為に、計 k 問を元の順序のまま並べる（新しい台詞は作らない）。
import { type VNLine, type VNScript } from '../vn/types';

interface Unit {
  act: number;
  lines: VNLine[];
}

function splitUnits(script: VNScript): Unit[] {
  const units: Unit[] = [];
  script.acts.forEach((act, ai) => {
    let cur: VNLine[] = [];
    act.lines.forEach((line, i) => {
      const prev = act.lines[i - 1];
      if (cur.length && line.speaker !== 'candidate' && prev?.speaker === 'candidate') {
        units.push({ act: ai, lines: cur });
        cur = [];
      }
      cur.push(line);
    });
    if (cur.length) units.push({ act: ai, lines: cur });
  });
  return units;
}

/** 決定的な疑似乱数（同じ seed なら同じ出題） */
function rng(seed: number): () => number {
  let s = (seed % 2147483646) + 1;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}

export function mockScript(script: VNScript, k: number, seed: number): VNScript {
  const units = splitUnits(script);
  const last = script.acts.length - 1;
  const fixed = (u: Unit) => u.act === 0 || u.act === last;
  const pool = units.map((u, i) => [u, i] as const).filter(([u]) => !fixed(u) && u.lines.some((l) => l.speaker === 'candidate'));
  const rand = rng(seed);
  // 層化抽出：中盤の各幕から最低 1 問（自己紹介・転職理由が落ちないように）、残りを無作為に
  const byAct = new Map<number, number[]>();
  for (const [u, i] of pool) byAct.set(u.act, [...(byAct.get(u.act) ?? []), i]);
  const shuffled = [...byAct.values()].map((ids) => ids.map((i) => [i, rand()] as const).sort((a, b) => a[1] - b[1]).map(([i]) => i));
  const picked = new Set<number>();
  for (const ids of shuffled) if (picked.size < k && ids.length) picked.add(ids[0]);
  const rest = shuffled.flatMap((ids) => ids.slice(1)).map((i) => [i, rand()] as const).sort((a, b) => a[1] - b[1]);
  for (const [i] of rest) {
    if (picked.size >= k) break;
    picked.add(i);
  }
  const keep = units.filter((u, i) => fixed(u) || picked.has(i));
  const acts = script.acts
    .map((act, ai) => ({ ...act, lines: keep.filter((u) => u.act === ai).flatMap((u) => u.lines) }))
    .filter((act) => act.lines.length > 0);
  return { ...script, acts };
}
