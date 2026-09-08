import { type VNScript } from './types';

// 公開範本：虛構占位台本。setup.sh 會複製本檔為 scripts.main.ts（該檔已 gitignore）。
// 要練你自己的真實面試台本：編輯本機的 scripts.main.ts，格式對照 types.ts，
// 內容可包含真實職涯敘事/期望年收等個資，該檔不會進版本控制。
export const SCRIPT_MAIN: VNScript = {
  id: 'dialog-probe',
  sample: true,
  titleJa: 'サンプル企業 PdM面接（対話・探測型）',
  titleZh: '範例企業 PdM 面試（雙向探測型）',
  interviewerJa: '採用マネージャー 山田',
  interviewerZh: '招聘經理 山田',
  acts: [
    {
      id: 1,
      titleJa: '第一幕　ご挨拶',
      titleZh: '第一幕　開場',
      goalJa: '面接は「見極め合う場」と設定する',
      goalZh: '把面試定位成互相確認的場合',
      tags: [{ ja: '第一印象', zh: '第一印象' }],
      lines: [
        {
          id: 'm1', speaker: 'interviewer', type: 'talk', emotion: 'smile',
          ja: '本日はお越しいただきありがとうございます。採用マネージャーの山田と申します。',
          zh: '今天感謝您前來面試。我是招聘經理山田。',
        },
        {
          id: 'm2', speaker: 'candidate', type: 'talk', emotion: 'neutral',
          ja: '本日は貴重なお時間をいただき、ありがとうございます。よろしくお願いいたします。',
          zh: '感謝您撥出寶貴的時間，請多多指教。',
        },
        {
          id: 'm3', speaker: 'inner', type: 'inner', emotion: 'think',
          ja: '（これは「選ばれる場」ではなく「お互いを知る場」だ。）',
          zh: '（這不是「被挑選的場合」，而是「互相了解的場合」。）',
        },
      ],
    },
    {
      id: 2,
      titleJa: '第二幕　自己紹介',
      titleZh: '第二幕　自我介紹',
      goalJa: '強みを一本の線で語る',
      goalZh: '把強項連成一條線',
      framework: {
        titleJa: '60秒自己紹介の型',
        titleZh: '60 秒自介的型',
        items: [
          { ja: '現在の役割（一言で）', zh: '現在的角色（一句話）' },
          { ja: '定量実績（数字で）', zh: '量化實績（用數字）' },
          { ja: '志望への接続', zh: '接到志望動機' },
        ],
      },
      lines: [
        {
          id: 'm4', speaker: 'interviewer', type: 'talk', emotion: 'neutral',
          ja: 'まず簡単に自己紹介をお願いできますか。',
          zh: '先請您簡單自我介紹。',
        },
        {
          id: 'm5', speaker: 'candidate', type: 'talk', emotion: 'neutral', favorDelta: 5,
          ja: '（ここに自分の経歴・定量実績・志望動機への接続を書き込みましょう）',
          zh: '（在這裡填入你自己的經歷、量化實績、與志望動機的連結）',
          note: {
            ja: 'サンプル台本のためプレースホルダー。scripts.main.ts で実際の内容に置き換えてください。',
            zh: '這是範本占位句，請在 scripts.main.ts 換成你自己的真實內容。',
          },
        },
      ],
    },
    {
      id: 3,
      titleJa: '第三幕　結び',
      titleZh: '第三幕　收尾',
      goalJa: '意欲を一言で伝える',
      goalZh: '一句話表達意願',
      lines: [
        {
          id: 'm6', speaker: 'candidate', type: 'talk', emotion: 'smile', favorDelta: 5,
          ja: '本日は貴重なお時間をいただき、ありがとうございました。',
          zh: '今天非常感謝您撥出寶貴的時間。',
        },
        {
          id: 'm7', speaker: 'interviewer', type: 'talk', emotion: 'smile',
          ja: 'こちらこそ、ありがとうございました。',
          zh: '我才要謝謝您。',
        },
      ],
    },
  ],
  report: {
    aspects: [
      {
        labelJa: '論理性', labelZh: '邏輯性', score: 3,
        commentJa: 'サンプル台本のため評価はダミーです。',
        commentZh: '這是範本占位評語。',
      },
      {
        labelJa: '具体性', labelZh: '具體性', score: 3,
        commentJa: 'サンプル台本のため評価はダミーです。',
        commentZh: '這是範本占位評語。',
      },
    ],
    overallJa: 'これはサンプル台本です。scripts.main.ts に実際の面接台本を書き込んでください。',
    overallZh: '這是範本占位台本，請在 scripts.main.ts 填入你自己的真實面試內容。',
  },
};
