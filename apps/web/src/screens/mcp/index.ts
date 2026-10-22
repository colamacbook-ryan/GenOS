import type { ComponentType } from 'react';
import { McpScreen } from './McpScreen';

/** Giai đoạn 4.3 · MCP Hub. */
export const SCREENS: Record<string, ComponentType> = {
  mcp: McpScreen,
};
