/**
 * Gerador de Pix BR Code (EMVCo / "Pix Copia e Cola"), conforme manual
 * BR Code 2.0 do BCB. Suporta valor fixo e dinâmico.
 *
 * O payload é um conjunto de campos TLV (id de 2 dígitos + tamanho de 2
 * dígitos + valor), terminando com CRC16-CCITT (poly 0x1021, init 0xFFFF).
 */

function emv(id: string, value: string): string {
  const len = value.length.toString().padStart(2, "0");
  return `${id}${len}${value}`;
}

function crc16(payload: string): string {
  let crc = 0xffff;
  for (let i = 0; i < payload.length; i++) {
    crc ^= payload.charCodeAt(i) << 8;
    for (let j = 0; j < 8; j++) {
      crc = crc & 0x8000 ? (crc << 1) ^ 0x1021 : crc << 1;
      crc &= 0xffff;
    }
  }
  return crc.toString(16).toUpperCase().padStart(4, "0");
}

export interface PixParams {
  key: string;             // chave Pix (email, CPF/CNPJ, telefone, aleatória)
  amount?: number;         // valor em BRL — omita para "doador escolhe"
  merchantName: string;    // até 25 chars
  merchantCity: string;    // até 15 chars
  txid?: string;           // identificador (default '***' = não exigido)
}

export function buildPixBRCode(p: PixParams): string {
  const merchantInfo = emv("00", "BR.GOV.BCB.PIX") + emv("01", p.key);
  const additional = emv("05", p.txid ?? "***");

  let out =
    emv("00", "01") +                                  // Payload Format Indicator
    emv("01", "12") +                                  // Point of Initiation = dynamic
    emv("26", merchantInfo) +                          // Merchant Account Info
    emv("52", "0000") +                                // MCC
    emv("53", "986");                                  // BRL

  if (p.amount !== undefined) {
    out += emv("54", p.amount.toFixed(2));
  }

  out +=
    emv("58", "BR") +                                  // Country
    emv("59", p.merchantName.slice(0, 25)) +           // Merchant Name
    emv("60", p.merchantCity.slice(0, 15)) +           // City
    emv("62", additional) +                            // Additional Data
    "6304";                                            // CRC ID + length placeholder

  return out + crc16(out);
}
