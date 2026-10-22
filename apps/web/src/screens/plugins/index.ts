import type { ComponentType } from 'react';
import { PluginsScreen } from './PluginsScreen';

/** Giai đoạn 4.4 · Plugin & Tiện ích. */
export const SCREENS: Record<string, ComponentType> = {
  plugins: PluginsScreen,
};
