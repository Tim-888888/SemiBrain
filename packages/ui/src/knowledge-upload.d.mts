export function documentFile(file: File): boolean
export function resolveImagePath(documentPath: string, reference: string): string | null
export function documentBundle(file: File, files: File[], documentPath: string): Promise<{ path: string; file: File }[]>
