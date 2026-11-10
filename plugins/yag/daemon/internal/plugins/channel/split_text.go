package channel

import (
	"strings"
	"unicode/utf8"
)

func splitTextParts(text string, limit int) []string {
	parts := make([]string, 0, 1)

	current := strings.Builder{}
	currentLen := 0

	flush := func() {
		if currentLen > 0 {
			parts = append(parts, current.String())
			current.Reset()
			currentLen = 0
		}
	}

	for line := range strings.SplitSeq(text, "\n") {
		need := utf8.RuneCountInString(line) + 1

		if need > limit {
			flush()
			parts = append(parts, chunksByRunes(line, limit)...)
			continue
		}

		if currentLen+need > limit {
			flush()
		}

		current.WriteString(line)
		current.WriteString("\n")
		currentLen += need
	}
	flush()

	return parts
}

func chunksByRunes(text string, size int) []string {
	runes := []rune(text)
	result := make([]string, 0, len(runes)/size+1)

	for start := 0; start < len(runes); start += size {
		end := min(start+size, len(runes))

		if end < len(runes) && runes[end-1] == '\\' {
			end--
		}

		result = append(result, string(runes[start:end]))
	}

	return result
}
