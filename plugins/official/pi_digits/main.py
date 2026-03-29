from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional


class PiDigitsPlugin:
    def __init__(self) -> None:
        self.name = "pi_digits"
        self.version = "1.1.0"
        self.description = "Trouve les chiffres de Pi a partir de positions dans ses decimales."

        # First decimals of Pi after the dot.
        self.pi_decimals = (
            "1415926535897932384626433832795028841971693993751058209749445923"
            "0781640628620899862803482534211706798214808651328230664709384460"
            "9550582231725359408128481117450284102701938521105559644622948954"
            "9303819644288109756659334461284756482337867831652712019091456485"
            "6692346034861045432664821339360726024914127372458700660631558817"
            "4881520920962829254091715364367892590360011330530548820466521384"
            "1469519415116094330572703657595919530921861173819326117931051185"
            "4807446237996274956735188575272489122793818301194912983367336244"
            "0656643086021394946395224737190702179860943702770539217176293176"
            "7523846748184676694051320005681271452635608277857713427577896091"
            "7363717872146844090122495343014654958537105079227968925892354201"
            "9956112129021960864034418159813629774771309960518707211349999998"
            "3729780499510597317328160963185950244594553469083026425223082533"
            "4468503526193118817101000313783875288658753320838142061717766914"
            "7303598253490428755468731159562863882353787593751957781857780532"
            "171226806613001927876611195909216420198"
        )
        self.max_position = len(self.pi_decimals)

    def get_pi_digit(self, position: int) -> Optional[str]:
        if position < 1 or position > self.max_position:
            return None
        return self.pi_decimals[position - 1]

    def parse_positions(self, text: str, allowed_chars: Optional[str] = None) -> List[int]:
        if allowed_chars is None:
            allowed_chars = " \t\r\n.A^oNSEW"

        cleaned_text = text
        for char in allowed_chars:
            cleaned_text = cleaned_text.replace(char, ' ')

        cleaned_text = cleaned_text.replace(',', ' ').replace(';', ' ').replace(':', ' ').replace('-', ' ')
        matches = re.findall(r'\d+', cleaned_text)

        positions: List[int] = []
        for match in matches:
            try:
                pos = int(match)
            except ValueError:
                continue
            if pos > 0:
                positions.append(pos)
        return positions

    def parse_axis_positions(self, text: str, allowed_chars: Optional[str] = None) -> Dict[str, List[int]]:
        axis_positions: Dict[str, List[int]] = {}
        line_pattern = re.compile(r'^\s*([NSEW])\s+(.+?)\s*$', re.IGNORECASE)

        for raw_line in text.splitlines():
            match = line_pattern.match(raw_line)
            if not match:
                continue
            axis = match.group(1).upper()
            payload = match.group(2).strip()
            positions = self.parse_positions(payload, allowed_chars)
            if positions:
                axis_positions[axis] = positions

        return axis_positions

    def _format_ddm_axis(self, axis: str, digits: str) -> Optional[str]:
        axis = (axis or '').upper()
        expected_deg_digits = 2 if axis in {'N', 'S'} else 3 if axis in {'E', 'W'} else 0
        if expected_deg_digits == 0:
            return None
        if len(digits) < expected_deg_digits + 3:
            return None

        degrees = digits[:expected_deg_digits]
        minutes_int = digits[expected_deg_digits:expected_deg_digits + 2]
        minutes_dec = digits[expected_deg_digits + 2:]
        if not minutes_dec:
            return None

        return f"{axis} {degrees}° {minutes_int}.{minutes_dec}'"

    def _build_coordinates_from_axis_digits(self, axis_digits: Dict[str, str]) -> Optional[Dict[str, Any]]:
        lat_axis = next((axis for axis in ('N', 'S') if axis in axis_digits), None)
        lon_axis = next((axis for axis in ('E', 'W') if axis in axis_digits), None)
        if not lat_axis or not lon_axis:
            return None

        ddm_lat = self._format_ddm_axis(lat_axis, axis_digits[lat_axis])
        ddm_lon = self._format_ddm_axis(lon_axis, axis_digits[lon_axis])
        if not ddm_lat or not ddm_lon:
            return None

        coordinates: Dict[str, Any] = {
            'exist': True,
            'ddm_lat': ddm_lat,
            'ddm_lon': ddm_lon,
            'ddm': f'{ddm_lat} {ddm_lon}',
        }

        try:
            from gc_backend.blueprints.coordinates import convert_ddm_to_decimal

            decimal_coords = convert_ddm_to_decimal(ddm_lat, ddm_lon)
            if isinstance(decimal_coords, dict):
                coordinates['decimal_latitude'] = decimal_coords.get('latitude')
                coordinates['decimal_longitude'] = decimal_coords.get('longitude')
        except Exception:
            pass

        return coordinates

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        text = inputs.get('text', '')
        mode = str(inputs.get('mode', 'decode')).lower()
        output_format = str(inputs.get('format', 'digits_only')).lower()
        allowed_chars = inputs.get('allowed_chars', " \t\r\n.A^oNSEW")

        standardized_response: Dict[str, Any] = {
            'status': 'success',
            'plugin_info': {
                'name': self.name,
                'version': self.version,
                'execution_time': 0,
            },
            'inputs': inputs.copy(),
            'results': [],
            'summary': {
                'best_result_id': None,
                'total_results': 0,
                'message': '',
            },
        }

        if not text:
            standardized_response['status'] = 'error'
            standardized_response['summary']['message'] = 'Aucun texte fourni a traiter.'
            standardized_response['plugin_info']['execution_time'] = int((time.time() - start_time) * 1000)
            return standardized_response

        if mode != 'decode':
            standardized_response['status'] = 'error'
            standardized_response['summary']['message'] = f"Mode non supporte: {mode}. Seul 'decode' est disponible."
            standardized_response['plugin_info']['execution_time'] = int((time.time() - start_time) * 1000)
            return standardized_response

        try:
            positions = self.parse_positions(text, allowed_chars)
            axis_positions = self.parse_axis_positions(text, allowed_chars)

            if not positions:
                standardized_response['status'] = 'error'
                standardized_response['summary']['message'] = 'Aucune position valide trouvee dans le texte.'
                standardized_response['plugin_info']['execution_time'] = int((time.time() - start_time) * 1000)
                return standardized_response

            results_data: List[Dict[str, Any]] = []
            valid_count = 0
            invalid_positions: List[int] = []

            for pos in positions:
                digit = self.get_pi_digit(pos)
                if digit is not None:
                    results_data.append({'position': pos, 'digit': digit})
                    valid_count += 1
                else:
                    invalid_positions.append(pos)

            if not results_data:
                standardized_response['status'] = 'error'
                standardized_response['summary']['message'] = (
                    f'Aucune position valide. Les positions doivent etre entre 1 et {self.max_position}.'
                )
                standardized_response['plugin_info']['execution_time'] = int((time.time() - start_time) * 1000)
                return standardized_response

            axis_positions_data: Dict[str, List[Dict[str, Any]]] = {}
            axis_digits: Dict[str, str] = {}
            for axis, axis_values in axis_positions.items():
                axis_items: List[Dict[str, Any]] = []
                for pos in axis_values:
                    digit = self.get_pi_digit(pos)
                    if digit is not None:
                        axis_items.append({'position': pos, 'digit': digit})
                if axis_items:
                    axis_positions_data[axis] = axis_items
                    axis_digits[axis] = ''.join(item['digit'] for item in axis_items)

            structured_coordinates = self._build_coordinates_from_axis_digits(axis_digits)
            raw_digits = ''.join(item['digit'] for item in results_data)

            if output_format == 'digits_only':
                text_output = structured_coordinates['ddm'] if structured_coordinates and structured_coordinates.get('ddm') else raw_digits
            elif output_format == 'positions_and_digits':
                if axis_positions_data:
                    chunks: List[str] = []
                    for axis in ('N', 'S', 'E', 'W'):
                        items = axis_positions_data.get(axis)
                        if items:
                            chunks.append(f"{axis} " + ' '.join(f"{item['position']}={item['digit']}" for item in items))
                    text_output = ' | '.join(chunks)
                else:
                    text_output = ' '.join(f"{item['position']}={item['digit']}" for item in results_data)
            else:
                lines = [f"Position {item['position']}: {item['digit']}" for item in results_data]
                if structured_coordinates and structured_coordinates.get('ddm'):
                    lines.extend(['', f"Coordonnees: {structured_coordinates['ddm']}"])
                text_output = '\n'.join(lines)

            metadata: Dict[str, Any] = {
                'total_positions': len(positions),
                'valid_positions': valid_count,
                'invalid_positions': invalid_positions if invalid_positions else None,
                'max_available_position': self.max_position,
                'positions_data': results_data,
                'axis_positions': axis_positions or None,
                'axis_positions_data': axis_positions_data or None,
                'axis_digits': axis_digits or None,
                'raw_digits_only': raw_digits,
                'structured_coordinates': structured_coordinates,
            }

            summary_msg = f'{valid_count} chiffre(s) de Pi trouve(s)'
            if invalid_positions:
                summary_msg += f" ({len(invalid_positions)} position(s) invalide(s) ignoree(s))"
            if structured_coordinates and structured_coordinates.get('ddm'):
                summary_msg += f" | coordonnees: {structured_coordinates['ddm']}"

            result_item: Dict[str, Any] = {
                'id': 'result_1',
                'text_output': text_output,
                'confidence': 1.0,
                'parameters': {
                    'mode': 'decode',
                    'format': output_format,
                },
                'metadata': metadata,
            }
            if structured_coordinates:
                result_item['coordinates'] = structured_coordinates
                result_item['decimal_latitude'] = structured_coordinates.get('decimal_latitude')
                result_item['decimal_longitude'] = structured_coordinates.get('decimal_longitude')

            standardized_response['results'].append(result_item)
            standardized_response['primary_coordinates'] = structured_coordinates
            standardized_response['summary'].update({
                'best_result_id': 'result_1',
                'total_results': 1,
                'message': summary_msg,
            })
        except Exception as exc:
            standardized_response['status'] = 'error'
            standardized_response['summary']['message'] = f'Erreur pendant le traitement : {exc}'

        standardized_response['plugin_info']['execution_time'] = int((time.time() - start_time) * 1000)
        return standardized_response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return PiDigitsPlugin().execute(inputs)
