import type { ComponentType } from 'react';
import { DirectoryScreen } from './DirectoryScreen';
import { DocumentsScreen } from './DocumentsScreen';
import { NotebookScreen } from './NotebookScreen';
import { ProfileScreen } from './ProfileScreen';

/** Giai đoạn 3 · Quan hệ & Đối tượng: màn đã dựng của cụm này, theo khoá trong danh mục (screens.ts). */
export const SCREENS: Record<string, ComponentType> = {
  directory: DirectoryScreen,
  profile: ProfileScreen,
  notebook: NotebookScreen,
  documents: DocumentsScreen,
};
