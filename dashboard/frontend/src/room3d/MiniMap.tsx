import { useEffect, useState } from 'react';
import { BOARDS, DOOR_Z, FLOOR_RECT, ROOMS, SCREEN, SEAT, type RoomId } from './layout';

const PX = 8;

interface MiniMapProps {
  title: string;
  current: RoomId;
  available: ReadonlySet<string>;
  roomName: (id: RoomId) => string;
  getPose: () => { x: number; z: number; yaw: number };
  onRoom: (id: RoomId) => void;
  onBoard: (id: string) => void;
  onSeat: () => void;
}

/** 樓層小地圖：點房間瞬移、點展示物飛過去、金點 = 面接席 */
export function MiniMap({ title, current, available, roomName, getPose, onRoom, onBoard, onSeat }: MiniMapProps) {
  const [pose, setPose] = useState(getPose());
  useEffect(() => {
    const id = setInterval(() => setPose({ ...getPose() }), 120);
    return () => clearInterval(id);
  }, [getPose]);

  const W = (FLOOR_RECT[2] - FLOOR_RECT[0]) * PX;
  const H = (FLOOR_RECT[3] - FLOOR_RECT[1]) * PX;
  const dir = { x: -Math.sin(pose.yaw), z: -Math.cos(pose.yaw) };

  return (
    <div className="r3d-minimap r3d-glass">
      <div className="r3d-minimap-title">{title}</div>
      <svg width={W + 4} height={H + 4} viewBox={`-2 -2 ${W + 4} ${H + 4}`} role="img" aria-label={title}>
        {ROOMS.map((r) => (
          <g key={r.id} className="room" onClick={() => onRoom(r.id)}>
            <rect
              x={r.rect[0] * PX} y={r.rect[1] * PX}
              width={(r.rect[2] - r.rect[0]) * PX} height={(r.rect[3] - r.rect[1]) * PX}
              fill={r.id === current ? 'rgba(245,200,66,.2)' : 'rgba(255,255,255,.05)'}
              stroke="rgba(255,255,255,.55)" strokeWidth={1.2}
            />
            <text x={((r.rect[0] + r.rect[2]) / 2) * PX} y={H / 2 - 4} textAnchor="middle" fontSize={11} fill="rgba(255,255,255,.75)">
              {roomName(r.id)}
            </text>
          </g>
        ))}
        {[8, 14, 22].map((x) => (
          <line key={x} x1={x * PX} x2={x * PX} y1={DOOR_Z[0] * PX} y2={DOOR_Z[1] * PX} stroke="#1d2733" strokeWidth={3} />
        ))}
        {BOARDS.map((b) => (
          <rect
            key={b.id} className="dot" onClick={() => onBoard(b.id)}
            x={b.x * PX - 5} y={b.z * PX - 2.5} width={10} height={5} rx={1.5}
            transform={b.nx ? `rotate(90 ${b.x * PX} ${b.z * PX})` : undefined}
            fill={available.has(b.id) ? '#9cc3ff' : 'rgba(255,255,255,.3)'}
          >
            <title>{b.id}</title>
          </rect>
        ))}
        <rect x={SCREEN.x * PX - 2.5} y={SCREEN.z * PX - 7} width={5} height={14} fill="#c9d3dd" />
        <circle className="dot" cx={SEAT.x * PX} cy={SEAT.z * PX} r={4} fill="#F5C842" onClick={onSeat} />
        <g transform={`translate(${pose.x * PX} ${pose.z * PX})`}>
          <line x1={0} y1={0} x2={dir.x * 11} y2={dir.z * 11} stroke="#ff8a7a" strokeWidth={2} strokeLinecap="round" />
          <circle r={3.6} fill="#ff6f61" stroke="#fff" strokeWidth={1.2} />
        </g>
      </svg>
    </div>
  );
}
