import type { ComponentType } from 'react';
import { DealsScreen } from './DealsScreen';
import { OpportunityScreen } from './OpportunityScreen';
import { SearchScreen } from './SearchScreen';
import { SupplyScreen } from './SupplyScreen';

/** Giai đoạn 3 · Cơ hội & Thị trường: màn đã dựng của cụm này, theo khoá trong danh mục (screens.ts). */
export const SCREENS: Record<string, ComponentType> = {
  opportunity: OpportunityScreen,
  supply: SupplyScreen,
  search: SearchScreen,
  deals: DealsScreen,
};
