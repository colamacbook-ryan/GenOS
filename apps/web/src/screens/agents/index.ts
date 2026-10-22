import type { ComponentType } from 'react';
import { AgentsScreen } from './AgentsScreen';

/** Giai đoạn 4.1 · Danh tính Agent. */
export const SCREENS: Record<string, ComponentType> = {
  agents: AgentsScreen,
};
