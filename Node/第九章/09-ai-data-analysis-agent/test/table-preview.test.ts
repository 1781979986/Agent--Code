import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { normalize, parseFile, previewSheet } from '../server/table-parser.js'

test('销售样例预览四条，实际导入全部十八条；各工作表独立计数', async () => {
  const bytes = await readFile(new URL('../samples/sales-demo.xlsx', import.meta.url))
  const sheets = await parseFile(bytes, 'sales-demo.xlsx')
  const sales = sheets.find(sheet => sheet.name === '销售明细')!
  const preview = previewSheet(sales)
  assert.equal(preview.rowCount, 18)
  assert.equal(preview.rows.length, 4)
  const imported = normalize(sales, preview.suggested)
  assert.equal(imported.rows.length, preview.rowCount)
  assert.deepEqual(imported.issues, [])
  const notes = previewSheet(sheets.find(sheet => sheet.name === '指标说明')!)
  assert.equal(notes.rowCount, 2)
  assert.equal(notes.rows.length, 2)
})

test('预览和导入都忽略空行，零金额仍计入数据', () => {
  const sheet = {
    name: '销售明细',
    cells: [
      ['销售日期', '区域', '商品名称', '实付金额（元）', '退款金额（元）'],
      [null, '', undefined, null, ''],
      ['2026-09-15', '华东', '咖啡机', 0, 0],
      [],
      ['2026-09-16', '华南', '键盘', 100, 0]
    ]
  }
  const preview = previewSheet(sheet)
  assert.equal(preview.rowCount, 2)
  assert.equal(preview.rows.length, 2)
  const imported = normalize(sheet, preview.suggested)
  assert.equal(imported.rows.length, preview.rowCount)
  assert.deepEqual(imported.rows.map(row => row.source_row), [3, 5])
})

test('只有表头或空行时，预览总数为零', () => {
  const preview = previewSheet({ name: '空表', cells: [['字段'], [null], ['']] })
  assert.equal(preview.rowCount, 0)
  assert.deepEqual(preview.rows, [])
})
