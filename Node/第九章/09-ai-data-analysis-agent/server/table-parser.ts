import ExcelJS from 'exceljs'
import { parse } from 'csv-parse/sync'
import { extname } from 'node:path'
import type { ColumnMap, Issue, SheetPreview } from '../shared/types.js'

const aliases: Record<keyof ColumnMap, string[]> = {
	date: ['销售日期', '日期', 'sold_at', 'date'],
	region: ['区域', '地区', 'region'],
	product: ['商品名称', '商品', 'product'],
	paid: ['实付金额（元）', '实付金额', 'paid_amount'],
	refund: ['退款金额（元）', '退款金额', 'refund_amount']
}
export type Matrix = { name: string; cells: unknown[][] }

/** 读取单行表头的销售表；支持 XLSX 和 UTF-8 CSV，公式保留为对象，后续明确报错，不采用缓存值。 */
export async function parseFile(
	bytes: Buffer,
	filename: string
): Promise<Matrix[]> {
	// 校验文件大小，要求文件非空且不超过 5 MiB。
	if (!bytes.length || bytes.length > 5 * 1024 * 1024)
		throw new Error('文件须为非空且不超过 5 MiB')

	// 获取文件扩展名，并统一转换为小写。
	const extension = extname(filename).toLowerCase()

	// CSV 文件按照 UTF-8 编码解析。
	if (extension === '.csv') {
		// 使用严格 UTF-8 解码，遇到非法编码时直接抛出异常。
		const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)

		// 将 CSV 文本解析为二维数组，每个子数组代表一行数据。
		const cells = parse(text, {
			bom: true, // 自动识别并移除 UTF-8 BOM。
			skip_empty_lines: true, // 跳过空行。
			max_record_size: 100000 // 限制单条 CSV 记录的最大字符数。
		}) as unknown[][]

		// 检查解析结果是否符合行数、列数等限制。
		checkSize(cells)

		// CSV 不包含工作表概念，统一使用 CSV 作为工作表名称。
		return [{ name: 'CSV', cells }]
	}

	// XLSX 文件必须具有正确的扩展名，并以 ZIP 文件标识 PK 开头。
	if (extension !== '.xlsx' || bytes[0] !== 0x50 || bytes[1] !== 0x4b)
		throw new Error('只支持 XLSX 与 UTF-8 CSV')

	// 创建 Excel 工作簿实例，并从文件字节中加载 XLSX 内容。
	const workbook = new ExcelJS.Workbook()
	await workbook.xlsx.load(bytes as any)

	// 遍历工作簿中的所有工作表，分别提取二维单元格数据。
	return workbook.worksheets.map((sheet) => {
		// 限制每张工作表最多 10000 行数据（另加 1 行表头）、50 列。
		if (sheet.rowCount > 10001 || sheet.columnCount > 50)
			throw new Error('每张表最多 10000 行数据、50 列')

		// 不允许存在合并单元格，避免影响后续表头识别和数据解析。
		if (Object.keys((sheet.model as any).merges ?? []).length)
			throw new Error('请先取消表头和数据区域的合并单元格')

		// 用二维数组保存当前工作表的所有单元格数据。
		const cells: unknown[][] = []

		// 遍历工作表中的每一行，包括空行。
		sheet.eachRow({ includeEmpty: true }, (row) => {
			// 按工作表的列数读取单元格，保证每行具有一致的列数。
			cells.push(
				Array.from(
					{ length: sheet.columnCount },
					(_, i) => row.getCell(i + 1).value // ExcelJS 的列索引从 1 开始。
				)
			)
		})

		// 检查当前工作表的数据规模是否符合限制。
		checkSize(cells)

		// 返回工作表名称及对应的二维单元格数据。
		// 公式单元格保留 ExcelJS 原始对象，不直接使用公式缓存结果。
		return { name: sheet.name, cells }
	})
}

function checkSize(cells: unknown[][]) {
	if (
		!cells.length ||
		cells.length > 10001 ||
		cells.some((row) => row.length > 50)
	)
		throw new Error('空表或超过 10000 行 / 50 列')
}

/** 返回数据总数和前四条预览，空行与实际导入保持一致，不计入总数。 */
export function previewSheet(sheet: Matrix): SheetPreview {
	const headers = sheet.cells[0].map((value) =>
		typeof value === 'string' ? value.trim() : ''
	)
	if (headers.some((h) => !h) || new Set(headers).size !== headers.length)
		throw new Error(`${sheet.name} 的首行须为不重复的文字表头`)
	const suggested = Object.fromEntries(
		Object.entries(aliases).map(([key, names]) => [
			key,
			headers.find((h) => names.includes(h)) || ''
		])
	) as ColumnMap
	const dataRows = sheet.cells
		.slice(1)
		.filter((row) => row.some((value) => value != null && value !== ''))
	return {
		name: sheet.name,
		headers,
		rowCount: dataRows.length,
		suggested,
		rows: dataRows
			.slice(0, 4)
			.map((row) =>
				row.map((value) =>
					value instanceof Date ? value.toISOString().slice(0, 10) : value
				)
			)
	}
}

/**
 * 校验并标准化销售数据；检查日期、金额、公式单元格和重复行，问题数据保留但阻止后续查询。
 */
export function normalize(sheet: Matrix, mapping: ColumnMap) {
	// 获取当前工作表的表头信息，用于校验字段映射是否合法。
	const { headers } = previewSheet(sheet)

	// 检查用户选择的字段是否都存在，并确保五个字段均为不同列。
	if (
		Object.values(mapping).some((h) => !headers.includes(h)) ||
		new Set(Object.values(mapping)).size !== 5
	)
		throw new Error('请选择五个不同且真实存在的字段；金额单位须为元')

	// 保存所有数据校验问题，不直接删除问题行，方便后续人工审核。
	const issues: Issue[] = []

	// 保存已经出现过的完整数据行，用于检测重复导入。
	const seen = new Map<string, number>()

	// 跳过第一行表头，从第二行开始处理实际数据。
	const rows = sheet.cells.slice(1).flatMap((cells, i) => {
		// 完全为空的行直接忽略。
		if (cells.every((v) => v == null || v === '')) return []

		// Excel 行号从 1 开始，当前数组索引需要加 2（跳过表头）。
		const row = i + 2

		// 根据字段映射获取当前行对应字段的原始值。
		const get = (field: keyof ColumnMap) =>
			cells[headers.indexOf(mapping[field])]

		// 保存标准化后的结果，source_row 用于追踪原始 Excel 行号。
		const result: Record<string, string | number | null> = { source_row: row }

		// 依次处理日期、金额、区域、商品等映射字段。
		for (const field of Object.keys(mapping) as (keyof ColumnMap)[]) {
			const value = get(field)

			try {
				// 必填字段不能为空。
				if (value == null || value === '') throw new Error('必填值为空')

				// ExcelJS 会将公式单元格解析为对象。
				// 不采用 Excel 缓存结果，要求用户先确认并导出真实数值。
				if (typeof value === 'object' && !(value instanceof Date))
					throw new Error('含公式或复杂单元格，请先核实并导出数值')

				// 处理销售日期字段。
				if (field === 'date') {
					// Date 类型转换为 YYYY-MM-DD，否则直接使用字符串。
					const text =
						value instanceof Date
							? value.toISOString().slice(0, 10)
							: String(value).trim()

					// 校验日期格式、日期合法性以及日期是否发生自动转换。
					if (
						!/^\d{4}-\d{2}-\d{2}$/.test(text) ||
						!Number.isFinite(Date.parse(text)) ||
						new Date(text).toISOString().slice(0, 10) !== text
					)
						throw new Error('日期须为有效 YYYY-MM-DD')

					// 保存标准化后的销售日期。
					result.sold_at = text
				}
				// 处理实付金额和退款金额字段。
				else if (field === 'paid' || field === 'refund') {
					// 去除金额中的千分位逗号，统一按数字处理。
					const text = String(value).trim().replaceAll(',', '')

					// 金额必须是非负数字，最多两位小数，并限制最大值。
					if (
						!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(text) ||
						Number(text) > 1000000000
					)
						throw new Error('金额须为非负数，最多两位小数，单位元')

					// 保存金额字段，统一保留两位小数。
					result[field === 'paid' ? 'paid_amount' : 'refund_amount'] =
						Number(text).toFixed(2)
				}
				// 处理区域和商品名称字段。
				else {
					result[field === 'product' ? 'product_name' : 'region'] =
						String(value).trim()
				}
			} catch (error) {
				// 当前字段校验失败，记录问题但继续保留该行。
				issues.push({
					row,
					field: mapping[field],
					message: (error as Error).message
				})
			}
		}

		// 检查退款金额是否超过实际支付金额。
		if (Number(result.refund_amount) > Number(result.paid_amount))
			issues.push({
				row,
				field: mapping.refund,
				message: '退款金额超过实付金额'
			})

		// 使用整行原始数据生成唯一标识，用于判断完全重复的数据。
		const key = JSON.stringify(cells)

		// 如果之前出现过相同数据，则记录重复问题，不自动删除。
		if (seen.has(key))
			issues.push({
				row,
				field: '整行',
				message: `与第 ${seen.get(key)} 行完全相同，请核对是否重复导入；未自动删除`
			})
		else seen.set(key, row)

		// 返回当前行的标准化结果。
		return [result]
	})

	// 没有任何有效数据时，直接拒绝创建数据集。
	if (!rows.length) throw new Error('所选工作表没有明细')

	// 返回标准化后的数据以及所有校验问题。
	return { rows, issues }
}
