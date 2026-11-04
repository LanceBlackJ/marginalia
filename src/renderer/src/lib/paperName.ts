/** 论文显示的名字：有备注名就用备注名，没有就用原标题。原标题不会被改。 */
export function paperName(p: { title: string; alias?: string | null }): string {
  return p.alias?.trim() || p.title
}
