import { Inject, Injectable } from '@nestjs/common'
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises'
import { join, basename } from 'node:path'
import { randomUUID, createHash } from 'node:crypto'
import { DuckDBInstance } from '@duckdb/node-api'
import { Storage } from './storage.js'
import { parseFile, previewSheet, normalize } from './table-parser.js'
import type { ColumnMap, Dataset, UploadPreview } from '../shared/types.js'

/** 保存上传原件、导入不可变数据版本；存在质量问题时关闭查询入口。 */
@Injectable()
export class DatasetService {
	constructor(@Inject(Storage) readonly storage: Storage) {}
	async list(): Promise<Dataset[]> {
		const directory = join(this.storage.directory, 'datasets')
		const names = await readdir(directory).catch(() => [] as string[])
		const datasets = await Promise.all(
			names.map((id) =>
				this.storage.dataset(id).catch((error: NodeJS.ErrnoException) => {
					if (error.code === 'ENOENT') return null
					throw error
				})
			)
		)
		return datasets
			.filter((d): d is Dataset => d !== null)
			.sort((a, b) => b.createdAt.localeCompare(a.createdAt))
	}
	/** 解析上传文件，保存原始文件及预览信息，返回上传结果。 */
	async upload(bytes: Buffer, filename: string): Promise<UploadPreview> {
		// 解析上传文件，提取其中的工作表数据。
		const sheets = await parseFile(bytes, filename)

		// 为本次上传生成唯一 ID，避免不同文件之间产生冲突。
		const id = randomUUID()

		// 根据上传 ID 创建独立的文件存储目录。
		const directory = join(this.storage.directory, 'uploads', id)
		await mkdir(directory, { recursive: true })

		// 保存原始文件内容，便于后续读取和处理。
		await writeFile(join(directory, 'source'), bytes)

		// 构建上传结果，将工作表数据转换为预览格式。
		const result = {
			id,
			filename: basename(filename), // 只保留文件名，去除可能包含的路径信息。
			sheets: sheets.map(previewSheet) // 将每个工作表转换为预览数据。
		}

		// 将预览信息保存为 JSON 文件，供后续查询和确认使用。
		await this.storage.save(join(directory, 'preview.json'), result)

		// 返回上传 ID、文件名及工作表预览信息。
		return result
	}

	/** 将上传文件中的指定工作表导入为数据集，完成数据清洗、去重、版本管理及数据库保存。 */
	async import(id: string, sheetName: string, mapping: ColumnMap) {
		// 校验上传 ID，再组成服务端路径，防止客户端指定任意文件位置。
		const { checkedId } = await import('./storage.js')
		const directory = join(this.storage.directory, 'uploads', checkedId(id))

		// 读取上传时保存的文件预览信息。
		const preview = await this.storage.load<UploadPreview>(
			join(directory, 'preview.json')
		)

		// 读取原始文件，并重新解析其中的工作表。
		const bytes = await readFile(join(directory, 'source'))
		const sheets = await parseFile(bytes, preview.filename)

		// 根据用户选择的工作表名称，查找对应的数据。
		const sheet = sheets.find((item) => item.name === sheetName)
		if (!sheet) throw new Error('所选工作表不存在')

		// 根据字段映射关系进行数据标准化，并收集清洗过程中发现的问题。
		const { rows, issues } = normalize(sheet, mapping)

		// 根据原始文件内容、工作表名称和字段映射生成 SHA-256 校验值。
		// 相同文件使用相同工作表和映射关系时，生成的校验值一致。
		const checksum = createHash('sha256')
			.update(bytes)
			.update(JSON.stringify([sheetName, mapping]))
			.digest('hex')

		// 获取已有数据集，根据校验值判断是否重复导入。
		const existing = await this.list()
		const repeated = existing.find((item) => item.checksum === checksum)

		// 如果已经存在相同的数据集，直接返回已有版本，避免重复保存。
		if (repeated) return { dataset: repeated, repeated: true }

		// 提取有效的销售日期，并按字符串升序排列，用于确定数据时间范围。
		const dates = rows
			.map((row) => row.sold_at)
			.filter((v): v is string => typeof v === 'string')
			.sort()

		// 构建本次导入的数据集元信息。
		const dataset: Dataset = {
			// 为新数据集生成唯一 ID。
			id: randomUUID(),

			// 保存原始文件名和所选工作表名称。
			name: preview.filename,
			sheet: sheetName,

			// 保存校验值，用于后续重复导入检查。
			checksum,

			// 同名文件、同名工作表的版本号递增，其他数据集不影响当前版本。
			version:
				1 +
				Math.max(
					0,
					...existing
						.filter((d) => d.name === preview.filename && d.sheet === sheetName)
						.map((d) => d.version)
				),

			// 记录数据集创建时间及标准化后的数据行数。
			createdAt: new Date().toISOString(),
			rowCount: rows.length,

			// 存在数据问题时标记为待审核，否则标记为可查询。
			status: issues.length ? 'needs_review' : 'ready',

			// 保存数据清洗时发现的问题，供用户检查。
			issues,

			// 记录数据集中最早和最晚的销售日期。
			start: dates[0] || null,
			end: dates.at(-1) || null,

			// 提取所有非空销售区域，并通过 Set 去重。
			regions: [
				...new Set(rows.map((r) => String(r.region || '')).filter(Boolean))
			],

			// 提取所有非空商品名称，并通过 Set 去重。
			products: [
				...new Set(
					rows.map((r) => String(r.product_name || '')).filter(Boolean)
				)
			],

			// 保存本次导入使用的字段映射，便于后续追溯。
			mapping
		}

		// 根据数据集 ID 创建独立的持久化目录。
		const target = this.storage.datasetDir(dataset.id)
		await mkdir(target, { recursive: true })

		// 保存原始文件副本，并根据文件类型保留对应扩展名。
		await writeFile(
			join(
				target,
				'source' +
					(preview.filename.toLowerCase().endsWith('.csv') ? '.csv' : '.xlsx')
			),
			bytes
		)

		// 将标准化后的数据保存为 JSON，供后续审核或处理使用。
		await this.storage.save(join(target, 'rows.json'), rows)

		// 只有不存在数据问题时，才创建可查询的 DuckDB 数据库。
		if (!issues.length) {
			// 为当前数据集创建独立的 DuckDB 数据库文件。
			const db = await DuckDBInstance.create(join(target, 'data.duckdb'))
			const connection = await db.connect()

			try {
				// 创建销售数据表，明确字段名称和数据库类型。
				await connection.run(
					'CREATE TABLE sales (source_row INTEGER, sold_at DATE, region VARCHAR, product_name VARCHAR, paid_amount DECIMAL(18,2), refund_amount DECIMAL(18,2))'
				)

				// 开启事务，保证本次批量插入操作的一致性。
				await connection.run('BEGIN TRANSACTION')

				// 逐行写入标准化后的销售数据，使用参数绑定避免 SQL 注入。
				for (const row of rows) {
					await connection.run('INSERT INTO sales VALUES ($1,$2,$3,$4,$5,$6)', [
						row.source_row,
						row.sold_at,
						row.region,
						row.product_name,
						row.paid_amount,
						row.refund_amount
					] as any)
				}

				// 所有数据插入成功后，提交事务。
				await connection.run('COMMIT')
			} finally {
				// 无论执行成功还是失败，都关闭数据库连接和实例。
				connection.closeSync()
				db.closeSync()
			}
		}

		// 最后保存数据集元信息，避免未完成的导入被页面列为可查询版本。
		await this.storage.save(join(target, 'metadata.json'), dataset)

		// 返回新创建的数据集，并标记本次不是重复导入。
		return { dataset, repeated: false }
	}
}
