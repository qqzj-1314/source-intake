import { z } from 'zod';
import { randomUUID } from 'node:crypto';
import { EngineError } from './engine.mjs';

const sourceSchema = z.object({
  role: z.enum(['source', 'attachment']).optional(),
  path: z.string().trim().min(1).max(4096).optional(),
  text: z.string().max(4_000_000).optional(),
  filename: z.string().trim().max(255).optional(),
  destination: z.string().trim().max(2048).optional(),
}).strict();

const routesSchema = z.object({
  csp: z.string().max(512).optional(),
  js: z.string().max(512).optional(),
  css: z.string().max(512).optional(),
  cls: z.string().max(512).optional(),
}).strict();

const requirementSchema = z.object({
  system: z.enum(['manual', 'BOS']).optional(),
  id: z.string().max(200).optional(),
  title: z.string().max(500).optional(),
  description: z.string().max(20000).optional(),
  prompt: z.string().max(100000).optional(),
  url: z.string().max(2000).optional(),
  updated_at: z.string().max(100).optional(),
}).strict();

export const previewSchema = z.object({
  session_id: z.string().min(8).max(128).optional(),
  root: z.string().trim().min(1).max(4096),
  project_name: z.string().max(200).optional(),
  profile: z.enum(['hisui', 'flat']).default('hisui'),
  routes: routesSchema.optional(),
  cls_layout: z.enum(['package', 'fullname']).default('package'),
  requirement: requirementSchema.optional(),
  sources: z.array(sourceSchema).max(200),
  work_package: z.boolean().default(false),
}).strict();

export const importSchema = z.object({
  preview_id: z.uuid(),
  conflict: z.enum(['skip', 'backup-replace']).default('skip'),
}).strict();

export const saveSettingsSchema = z.object({
  root: z.string().trim().min(1).max(4096),
  project_name: z.string().max(200).optional(),
  profile: z.enum(['hisui', 'flat']).default('hisui'),
  routes: routesSchema.optional(),
  cls_layout: z.enum(['package', 'fullname']).default('package'),
}).strict();

export function buildRequest(input) {
  const sources = input.sources.map(source => {
    const kindCount = (source.path ? 1 : 0) + (source.text !== undefined ? 1 : 0);
    if (kindCount !== 1) {
      throw new EngineError('每个来源必须且只能提供文件路径或粘贴文本之一。');
    }
    if (source.text !== undefined && !source.text.trim()) {
      throw new EngineError('粘贴内容为空，请补充源码后重新预览。');
    }
    const spec = {};
    if (source.role) spec.role = source.role;
    if (source.path) spec.path = source.path.trim();
    else spec.text = source.text;
    if (source.filename?.trim()) spec.filename = source.filename.trim();
    if (source.destination?.trim()) spec.destination = source.destination.trim();
    return spec;
  });
  return {
    root: input.root.trim(),
    project_name: input.project_name?.trim() || '',
    profile: input.profile,
    routes: input.routes ?? {},
    cls_layout: input.cls_layout,
    requirement: {
      system: input.requirement?.system ?? 'manual',
      id: input.requirement?.id?.trim() || '',
      title: input.requirement?.title?.trim() || '',
      description: input.requirement?.description ?? '',
      prompt: input.requirement?.prompt ?? '',
      url: input.requirement?.url?.trim() || '',
      updated_at: input.requirement?.updated_at?.trim() || '',
    },
    sources,
    ...(input.work_package ? { work_package: { token: randomUUID().replaceAll('-', '') } } : {}),
  };
}
