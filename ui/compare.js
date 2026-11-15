/* Q87: 결과의 출처를 선택 전까지 가리고, 골든 샘플을 기준선으로 섞는다. */
const $c = (id) => document.getElementById(id);
let trial = null;

async function candidate(suffix, source) {
  const file = $c(`file-${suffix}`).files[0];
  const body = $c(`text-${suffix}`).value.trim();
  if (!file && !body) throw new Error(`${source} 결과를 입력하세요.`);
  if (!file) return { source, type: "text", content: body };
  if (!file.type.startsWith("image/")) throw new Error(`${source}: 이미지 파일만 받을 수 있습니다.`);
  const content = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error(`${source} 파일을 읽지 못했습니다.`));
    reader.readAsDataURL(file);
  });
  return { source, type: "image", content, filename: file.name };
}

function shuffle(rows) {
  const out = [...rows];
  for (let i = out.length - 1; i > 0; i--) {
    const n = new Uint32Array(1); crypto.getRandomValues(n);
    const j = n[0] % (i + 1);
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

$c("start-compare").onclick = async () => {
  $c("compare-error").textContent = "";
  const set = $c("set-id").value.trim();
  const task = $c("task").value.trim();
  const a = $c("version-a").value.trim();
  const b = $c("version-b").value.trim();
  if (!set || !task || !a || !b || a === b) {
    $c("compare-error").textContent = "세트, 작업, 서로 다른 두 프롬프트 버전을 입력하세요.";
    return;
  }
  try {
    const rows = await Promise.all([candidate("a", a), candidate("b", b), candidate("golden", "골든 샘플")]);
    trial = { set, task, versions: [a, b], rows: shuffle(rows), chosen: null };
    const cards = $c("cards"); cards.replaceChildren();
    trial.rows.forEach((row, i) => {
      const card = document.createElement("article"); card.className = "compare-card";
      const heading = document.createElement("h3"); heading.textContent = `결과 ${i + 1}`; card.append(heading);
      if (row.type === "image") {
        const img = document.createElement("img"); img.src = row.content; img.alt = `결과 ${i + 1}`; card.append(img);
      } else {
        const pre = document.createElement("pre"); pre.textContent = row.content; card.append(pre);
      }
      const label = document.createElement("label");
      const radio = document.createElement("input"); radio.type = "radio"; radio.name = "pick"; radio.value = String(i);
      label.append(radio, document.createTextNode(" 이 결과 선택")); card.append(label); cards.append(card);
    });
    $c("setup").hidden = true; $c("blind").hidden = false;
  } catch (e) { $c("compare-error").textContent = e.message; }
};

$c("reveal").onclick = () => {
  const picked = document.querySelector('input[name="pick"]:checked');
  if (!picked) { $c("choice-error").textContent = "결과 하나를 선택하세요."; return; }
  trial.chosen = Number(picked.value);
  $c("revealed").replaceChildren();
  trial.rows.forEach((row, i) => {
    const p = document.createElement("p");
    p.textContent = `결과 ${i + 1}: ${row.source}${i === trial.chosen ? " · 선택됨" : ""}`;
    $c("revealed").append(p);
  });
  $c("blind").hidden = true; $c("result").hidden = false;
};

$c("download").onclick = () => {
  if (!trial || trial.chosen === null) return;
  const report = { set: trial.set, task: trial.task, versions: trial.versions,
    order: trial.rows.map((r) => r.source), selected: trial.rows[trial.chosen].source };
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = "assets-blind-comparison.json"; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

$c("again").onclick = () => {
  trial = null; $c("result").hidden = true; $c("setup").hidden = false;
};
