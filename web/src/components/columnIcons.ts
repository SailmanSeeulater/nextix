import {
  Activity,
  CircleCheckBig,
  CircleDashed,
  GitPullRequest,
  type LucideIcon,
  MessageCircleQuestionMark,
  TriangleAlert,
} from "lucide-react";
import type { Column } from "@/lib/types";

/** Every state has a glyph as well as a color, so color never carries state alone. */
export const COLUMN_ICONS: Record<Column, LucideIcon> = {
  todo: CircleDashed,
  doing: Activity,
  needs_input: MessageCircleQuestionMark,
  failed: TriangleAlert,
  in_review: GitPullRequest,
  done: CircleCheckBig,
};
