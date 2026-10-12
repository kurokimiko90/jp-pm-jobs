import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { en } from './locales/en';
import { ja, type TKey, type TranslationDict } from './locales/ja';
import { zh } from './locales/zh';

export type Lang = 'zh' | 'ja' | 'en';

const STORAGE_KEY = 'jp-pm-jobs.lang';

const dictionaries: Record<Lang, TranslationDict> = { zh, ja, en };

const isLang = (value: string | null): value is Lang =>
  value === 'zh' || value === 'ja' || value === 'en';

const detectLang = (): Lang => {
  if (typeof window === 'undefined') return 'ja';
  const saved = window.localStorage.getItem(STORAGE_KEY);
  if (isLang(saved)) return saved;
  const browser = window.navigator.language.toLowerCase();
  if (browser.startsWith('zh')) return 'zh';
  if (browser.startsWith('en')) return 'en';
  return 'ja';
};

export const getLangStatic = (): Lang => detectLang();

export const getDict = (lang: Lang): TranslationDict => ({ ...ja, ...dictionaries[lang] });

export const tStatic = (key: TKey, lang: Lang = getLangStatic()): string => getDict(lang)[key];

interface LangContextType {
  lang: Lang;
  setLang: (l: Lang) => void;
}

const LangCtx = createContext<LangContextType>({
  lang: 'ja',
  setLang: () => {},
});

export const LangProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [lang, setLang] = useState<Lang>(detectLang);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, lang);
      document.documentElement.lang = lang === 'zh' ? 'zh-Hant' : lang;
    }
  }, [lang]);

  return (
    <LangCtx.Provider value={{ lang, setLang }}>
      {children}
    </LangCtx.Provider>
  );
};

export const useLang = () => useContext(LangCtx);

export const useT = (): TranslationDict => {
  const { lang } = useLang();
  return useMemo(() => getDict(lang), [lang]);
};

export type { TKey, TranslationDict };
