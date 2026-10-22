import type { ComponentType } from 'react';
import { InboxScreen } from './InboxScreen';
import { OverviewScreen } from './OverviewScreen';
import { TasksScreen } from './TasksScreen';
import { WorkbenchScreen } from './WorkbenchScreen';

/** Giai đoạn 3 · Hàng đợi & Hành động: màn đã dựng của cụm này, theo khoá trong danh mục (screens.ts). */
export const SCREENS: Record<string, ComponentType> = {
  overview: OverviewScreen,
  inbox: InboxScreen,
  workbench: WorkbenchScreen,
  tasks: TasksScreen,
};
