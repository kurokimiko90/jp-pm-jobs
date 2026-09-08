import React from 'react';
import { type VNEmotion } from './types';

// CSS/SVG 幾何造型頭像：面試官（西裝+眼鏡）與候選人。表情隨 emotion 切換。

interface AvatarProps {
  persona: 'interviewer' | 'candidate';
  emotion: VNEmotion;
  /** 說話中：嘴巴開合動畫 */
  talking?: boolean;
  size?: number;
}

const SKIN = '#F2D8BE';
const SKIN_SHADE = '#E3C1A2';

interface FaceParts {
  browL: string;
  browR: string;
  eyes: React.ReactNode;
  mouth: React.ReactNode;
  sweat?: boolean;
  blush?: boolean;
}

const eyeDots = (r: number, dy = 0): React.ReactNode => (
  <>
    <circle cx={48} cy={50 + dy} r={r} fill="#3A322B" />
    <circle cx={72} cy={50 + dy} r={r} fill="#3A322B" />
    <circle cx={49.2} cy={48.8 + dy} r={r * 0.32} fill="#FFF" />
    <circle cx={73.2} cy={48.8 + dy} r={r * 0.32} fill="#FFF" />
  </>
);

const eyeArcs = (dir: 'up' | 'down'): React.ReactNode => {
  const bend = dir === 'up' ? -4.5 : 4;
  return (
    <>
      <path d={`M43 50 Q48 ${50 + bend} 53 50`} stroke="#3A322B" strokeWidth={2.4} fill="none" strokeLinecap="round" />
      <path d={`M67 50 Q72 ${50 + bend} 77 50`} stroke="#3A322B" strokeWidth={2.4} fill="none" strokeLinecap="round" />
    </>
  );
};

const eyeLids = (): React.ReactNode => (
  <>
    <line x1={43.5} y1={50} x2={52.5} y2={50} stroke="#3A322B" strokeWidth={3} strokeLinecap="round" />
    <line x1={67.5} y1={50} x2={76.5} y2={50} stroke="#3A322B" strokeWidth={3} strokeLinecap="round" />
  </>
);

const getFace = (emotion: VNEmotion): FaceParts => {
  switch (emotion) {
    case 'smile':
      return {
        browL: 'M41 40 Q47 37.5 53 39.5', browR: 'M67 39.5 Q73 37.5 79 40',
        eyes: eyeArcs('up'),
        mouth: <path d="M52 65 Q60 71.5 68 65" stroke="#B5654E" strokeWidth={2.6} fill="none" strokeLinecap="round" />,
      };
    case 'think':
      return {
        browL: 'M41 41.5 Q47 40 53 41', browR: 'M67 38.5 Q73 35.5 79 38',
        eyes: eyeDots(3, -1.5),
        mouth: <line x1={55} y1={66.5} x2={64} y2={65.5} stroke="#B5654E" strokeWidth={2.4} strokeLinecap="round" />,
      };
    case 'serious':
      return {
        browL: 'M41 38 L53 41.5', browR: 'M67 41.5 L79 38',
        eyes: eyeLids(),
        mouth: <path d="M52.5 67 Q60 65 67.5 67" stroke="#9C5340" strokeWidth={2.6} fill="none" strokeLinecap="round" />,
      };
    case 'nervous':
      return {
        browL: 'M41 42.5 Q47 39.5 53 38.5', browR: 'M67 38.5 Q73 39.5 79 42.5',
        eyes: eyeDots(3.6),
        mouth: <path d="M53 66 Q56.5 64.5 60 66 Q63.5 67.5 67 66" stroke="#9C5340" strokeWidth={2.3} fill="none" strokeLinecap="round" />,
        sweat: true,
      };
    case 'relieved':
      return {
        browL: 'M41 39.5 Q47 37.8 53 39', browR: 'M67 39 Q73 37.8 79 39.5',
        eyes: eyeArcs('down'),
        mouth: <path d="M53 65 Q60 70 67 65 Q60 67.8 53 65 Z" fill="#B5654E" />,
        blush: true,
      };
    default:
      return {
        browL: 'M41 40.5 Q47 39 53 40', browR: 'M67 40 Q73 39 79 40.5',
        eyes: eyeDots(3.2),
        mouth: <line x1={54.5} y1={66} x2={65.5} y2={66} stroke="#B5654E" strokeWidth={2.4} strokeLinecap="round" />,
      };
  }
};

export const VNAvatar: React.FC<AvatarProps> = ({ persona, emotion, talking = false, size = 150 }) => {
  const face = getFace(emotion);
  const isInterviewer = persona === 'interviewer';
  const hair = isInterviewer ? '#57504A' : '#3E3630';
  const suit = isInterviewer ? '#3E5C8A' : '#55493E';
  const suitDark = isInterviewer ? '#33496D' : '#443A31';
  const accent = isInterviewer ? '#C79A1B' : '#C77F8C';

  return (
    <svg viewBox="0 0 120 128" width={size} height={size * (128 / 120)} aria-hidden="true">
      {/* 肩・スーツ */}
      <path d={'M18 128 C20 100 38 92 60 92 C82 92 100 100 102 128 Z'} fill={suit} />
      <path d={'M52 92 L60 106 L68 92 L64 90 L56 90 Z'} fill="#FDF9F2" />
      <path d={'M18 128 C19 104 30 95 44 92.5 L52 110 L46 128 Z'} fill={suitDark} />
      <path d={'M102 128 C101 104 90 95 76 92.5 L68 110 L74 128 Z'} fill={suitDark} />
      {isInterviewer
        ? <path d="M60 106 L56.5 97 L60 94 L63.5 97 Z" fill={accent} />
        : <circle cx={60} cy={101} r={3.2} fill={accent} />}
      {/* 首 */}
      <rect x={53} y={76} width={14} height={18} rx={6} fill={SKIN_SHADE} />
      {/* 顔 */}
      <circle cx={60} cy={52} r={30} fill={SKIN} />
      {/* 髪 */}
      {isInterviewer ? (
        <path d="M30 52 C29 28 44 20 60 20 C76 20 91 28 90 52 C90 44 84 38 79 38 C70 38 68 33 60 33 C52 33 50 38 41 38 C36 38 30 44 30 52 Z" fill={hair} />
      ) : (
        <path d="M30 54 C28 27 44 19 60 19 C76 19 92 27 90 54 C88 42 83 36 76 36 C68 36 66 30 58 31 C48 32 47 39 40 40 C34 41 31 47 30 54 Z" fill={hair} />
      )}
      {/* 眉 */}
      <path d={face.browL} stroke={hair} strokeWidth={2.6} fill="none" strokeLinecap="round" />
      <path d={face.browR} stroke={hair} strokeWidth={2.6} fill="none" strokeLinecap="round" />
      {/* 目 */}
      {face.eyes}
      {/* 眼鏡（面接官のみ） */}
      {isInterviewer && (
        <g stroke="#4B443D" strokeWidth={1.8} fill="none" opacity={0.85}>
          <rect x={40} y={43.5} width={16} height={12.5} rx={5} />
          <rect x={64} y={43.5} width={16} height={12.5} rx={5} />
          <line x1={56} y1={49} x2={64} y2={49} />
        </g>
      )}
      {/* 口（說話中開合） */}
      <g style={talking
        ? { animation: 'vnTalkMouth 230ms ease-in-out infinite alternate', transformBox: 'fill-box', transformOrigin: 'center' } as React.CSSProperties
        : undefined}>
        {face.mouth}
      </g>
      {/* 汗 / 頬の赤み */}
      {face.sweat && (
        <path d="M88 34 Q92.5 41 88 44 Q83.5 41 88 34 Z" fill="#9CC8E8" opacity={0.9}>
          <animate attributeName="opacity" values="0.9;0.45;0.9" dur="1.6s" repeatCount="indefinite" />
        </path>
      )}
      {face.blush && (
        <>
          <ellipse cx={42} cy={62} rx={5} ry={2.6} fill="#F2B5C0" opacity={0.55} />
          <ellipse cx={78} cy={62} rx={5} ry={2.6} fill="#F2B5C0" opacity={0.55} />
        </>
      )}
    </svg>
  );
};
