import type { ComponentType } from 'react';
import { CareScreen } from './CareScreen';
import { PeopleScreen } from './PeopleScreen';

/** Giai đoạn 3 · Con người & Chất lượng: màn đã dựng của cụm này, theo khoá trong danh mục (screens.ts). */
export const SCREENS: Record<string, ComponentType> = {
  people: PeopleScreen,
  care: CareScreen,
};
