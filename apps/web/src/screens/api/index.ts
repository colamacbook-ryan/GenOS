import type { ComponentType } from 'react';
import { ApiScreen } from './ApiScreen';

/** Giai đoạn 4.2 · API & Model. */
export const SCREENS: Record<string, ComponentType> = {
  api: ApiScreen,
};
