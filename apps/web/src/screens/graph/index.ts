import type { ComponentType } from 'react';
import { GraphScreen } from './GraphScreen';

/** Giai đoạn 3 · Bản đồ quan hệ: màn đã dựng của cụm này, theo khoá trong danh mục (screens.ts). */
export const SCREENS: Record<string, ComponentType> = {
  graph: GraphScreen,
};
