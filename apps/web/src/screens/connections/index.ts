import type { ComponentType } from 'react';
import { ConnectionsScreen } from './ConnectionsScreen';

/** v0.1.42 (F-7) · Kết nối. */
export const SCREENS: Record<string, ComponentType> = {
  connections: ConnectionsScreen,
};
